from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document, DocumentChange, DocumentVersion


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def find_by_normalized_url(self, normalized_url: str) -> Document | None:
        """FOR UPDATE (spec Phase 7 section 30, same pattern as the Phase 6
        FetchStrategyStats/URLPatternStats fix): the only caller,
        crawl_engine.py, reads this row and then -- if it exists -- mutates
        it and appends a version a few lines later in the same
        transaction. Locking here serializes two concurrent crawls of the
        same URL (e.g. an instant crawl racing a scheduled monitoring
        crawl) so the second writer sees the first writer's committed
        state instead of a stale in-memory copy, closing the same lost-
        update class of bug rather than reopening it for documents."""
        result = await self._session.execute(
            select(Document)
            .where(Document.normalized_url == normalized_url)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def find_by_normalized_hash(self, normalized_hash: str, *, exclude_id: uuid.UUID | None = None) -> Document | None:
        stmt = select(Document).where(Document.normalized_hash == normalized_hash)
        if exclude_id is not None:
            stmt = stmt.where(Document.id != exclude_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, **fields) -> Document:
        document_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        fields.setdefault("collected_at", now)
        fields.setdefault("change_status", "NEW")
        fields.setdefault("storage_backend", "minio")
        document = Document(id=document_id, document_id=document_id, **fields)
        self._session.add(document)
        await self._session.flush()
        return document

    async def add_version(self, document: Document, **fields) -> DocumentVersion:
        """No separate table anymore -- `versions` is a JSONB list on the
        document row itself. Reassigns a new list (never appends in place)
        so SQLAlchemy's JSONB change tracking fires."""
        version = DocumentVersion(id=uuid.uuid4(), document_id=document.id, **fields)
        if version.created_at is None:
            version.created_at = datetime.now(timezone.utc)
        document.versions = [*(document.versions or []), _version_to_dict(version)]
        # Keep dense artifact pointers on the document row (latest version).
        if version.storage_key is not None:
            document.storage_key = version.storage_key
        if version.raw_content_type is not None:
            document.raw_content_type = version.raw_content_type
        if version.raw_size_bytes is not None:
            document.raw_size_bytes = version.raw_size_bytes
        if version.raw_sha256 is not None:
            document.raw_sha256 = version.raw_sha256
        if version.raw_artifact_id is not None:
            document.raw_artifact_id = version.raw_artifact_id
        await self._session.flush()
        return version

    async def commit(self) -> None:
        await self._session.commit()

    async def get(self, document_id: uuid.UUID) -> Document | None:
        result = await self._session.execute(select(Document).where(Document.id == document_id))
        return result.scalar_one_or_none()

    async def get_locked(self, document_id: uuid.UUID) -> Document | None:
        result = await self._session.execute(
            select(Document)
            .where(Document.id == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_versions(self, document_id: uuid.UUID) -> list[DocumentVersion]:
        document = await self.get(document_id)
        if document is None:
            return []
        versions = [_version_from_entry(entry) for entry in (document.versions or [])]
        return sorted(versions, key=lambda v: v.version_number)

    async def get_version(self, document_id: uuid.UUID, version_number: int) -> DocumentVersion | None:
        for version in await self.list_versions(document_id):
            if version.version_number == version_number:
                return version
        return None

    async def list_documents(
        self,
        *,
        crawl_job_id: uuid.UUID | None = None,
        source_id: uuid.UUID | None = None,
        domain: str | None = None,
        page_type: str | None = None,
        limit: int = 50,
    ) -> list[Document]:
        stmt = select(Document).order_by(Document.collected_at.desc()).limit(limit)
        if crawl_job_id is not None:
            stmt = stmt.where(Document.crawl_job_id == crawl_job_id)
        if source_id is not None:
            stmt = stmt.where(Document.source_id == source_id)
        if domain is not None:
            stmt = stmt.where(Document.domain == domain)
        if page_type is not None:
            stmt = stmt.where(Document.page_type == page_type)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_source(self, source_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Document).where(Document.source_id == source_id)
        )
        return result.scalar_one()

    # -- Phase 7: structured change events --

    async def add_change(self, document_id: uuid.UUID, **fields) -> DocumentChange:
        """No separate table anymore -- `changes` is a JSONB list on the
        document row. Caller (crawl_engine.py) already holds the document
        row locked/loaded in the same transaction as add_version above."""
        document = await self.get(document_id)
        if document is None:
            raise ValueError(f"document {document_id} not found")
        change = DocumentChange(id=uuid.uuid4(), document_id=document_id, **fields)
        if change.created_at is None:
            change.created_at = datetime.now(timezone.utc)
        document.changes = [*(document.changes or []), _change_to_dict(change)]
        document.change_status = "UPDATED"
        document.latest_change_type = change.change_type
        document.latest_change_severity = change.severity
        document.latest_change_similarity = change.similarity
        document.latest_change_confidence = change.change_confidence
        document.latest_changed_fields = list(change.changed_fields or [])
        document.latest_change_diff = dict(change.diff or {})
        document.latest_change_reasons = list(change.reasons or [])
        await self._session.flush()
        return change

    async def list_changes(
        self,
        document_id: uuid.UUID,
        *,
        severity: str | None = None,
        change_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
    ) -> list[DocumentChange]:
        document = await self.get(document_id)
        if document is None:
            return []
        changes = [_change_from_entry(entry) for entry in (document.changes or [])]
        if severity is not None:
            changes = [c for c in changes if c.severity == severity]
        if change_type is not None:
            changes = [c for c in changes if c.change_type == change_type]
        if since is not None:
            changes = [c for c in changes if c.created_at is not None and c.created_at >= since]
        if until is not None:
            changes = [c for c in changes if c.created_at is not None and c.created_at <= until]
        changes.sort(key=lambda c: c.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return changes[:limit]

    async def get_latest_change(self, document_id: uuid.UUID) -> DocumentChange | None:
        changes = await self.list_changes(document_id, limit=1)
        return changes[0] if changes else None


def _parse_uuid(value) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _parse_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value))


def _version_from_entry(entry: dict) -> DocumentVersion:
    return DocumentVersion(
        id=_parse_uuid(entry["id"]),
        document_id=_parse_uuid(entry["document_id"]),
        version_number=int(entry["version_number"]),
        change_type=entry["change_type"],
        title=entry.get("title"),
        content=entry.get("content") or "",
        content_hash=entry.get("content_hash") or "",
        normalized_hash=entry.get("normalized_hash") or "",
        extraction_metadata=entry.get("extraction_metadata") or {},
        created_at=_parse_datetime(entry.get("created_at")),
        raw_artifact_id=_parse_uuid(entry.get("raw_artifact_id")),
        storage_key=entry.get("storage_key"),
        raw_content_type=entry.get("raw_content_type"),
        raw_size_bytes=entry.get("raw_size_bytes"),
        raw_sha256=entry.get("raw_sha256"),
    )


def _change_from_entry(entry: dict) -> DocumentChange:
    return DocumentChange(
        id=_parse_uuid(entry["id"]),
        document_id=_parse_uuid(entry["document_id"]),
        change_type=entry["change_type"],
        severity=entry["severity"],
        previous_version_id=_parse_uuid(entry.get("previous_version_id")),
        current_version_id=_parse_uuid(entry.get("current_version_id")),
        page_type=entry.get("page_type"),
        similarity=float(entry.get("similarity") or 0.0),
        change_confidence=float(entry.get("change_confidence") or 0.0),
        changed_fields=list(entry.get("changed_fields") or []),
        diff=dict(entry.get("diff") or {}),
        reasons=list(entry.get("reasons") or []),
        created_at=_parse_datetime(entry.get("created_at")),
    )


def _version_to_dict(version: DocumentVersion) -> dict:
    return {
        "id": str(version.id),
        "document_id": str(version.document_id),
        "version_number": version.version_number,
        "change_type": version.change_type,
        "title": version.title,
        "content": version.content,
        "content_hash": version.content_hash,
        "normalized_hash": version.normalized_hash,
        "extraction_metadata": version.extraction_metadata,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "raw_artifact_id": str(version.raw_artifact_id) if version.raw_artifact_id else None,
        "storage_key": version.storage_key,
        "raw_content_type": version.raw_content_type,
        "raw_size_bytes": version.raw_size_bytes,
        "raw_sha256": version.raw_sha256,
    }


def _change_to_dict(change: DocumentChange) -> dict:
    return {
        "id": str(change.id),
        "document_id": str(change.document_id),
        "change_type": change.change_type,
        "severity": change.severity,
        "previous_version_id": str(change.previous_version_id) if change.previous_version_id else None,
        "current_version_id": str(change.current_version_id) if change.current_version_id else None,
        "page_type": change.page_type,
        "similarity": change.similarity,
        "change_confidence": change.change_confidence,
        "changed_fields": change.changed_fields,
        "diff": change.diff,
        "reasons": change.reasons,
        "created_at": change.created_at.isoformat() if change.created_at else None,
    }
