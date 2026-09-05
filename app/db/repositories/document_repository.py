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
        document = Document(id=uuid.uuid4(), **fields)
        self._session.add(document)
        await self._session.flush()
        return document

    async def add_version(self, document: Document, **fields) -> DocumentVersion:
        version = DocumentVersion(id=uuid.uuid4(), document_id=document.id, **fields)
        self._session.add(version)
        await self._session.flush()
        return version

    async def commit(self) -> None:
        await self._session.commit()

    async def get(self, document_id: uuid.UUID) -> Document | None:
        result = await self._session.execute(select(Document).where(Document.id == document_id))
        return result.scalar_one_or_none()

    async def list_versions(self, document_id: uuid.UUID) -> list[DocumentVersion]:
        result = await self._session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number)
        )
        return list(result.scalars().all())

    async def get_version(self, document_id: uuid.UUID, version_number: int) -> DocumentVersion | None:
        result = await self._session.execute(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.version_number == version_number,
            )
        )
        return result.scalar_one_or_none()

    async def list_documents(
        self,
        *,
        crawl_job_id: uuid.UUID | None = None,
        domain: str | None = None,
        page_type: str | None = None,
        limit: int = 50,
    ) -> list[Document]:
        stmt = select(Document).order_by(Document.collected_at.desc()).limit(limit)
        if crawl_job_id is not None:
            stmt = stmt.where(Document.crawl_job_id == crawl_job_id)
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

    async def add_change(self, **fields) -> DocumentChange:
        change = DocumentChange(id=uuid.uuid4(), **fields)
        self._session.add(change)
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
        stmt = (
            select(DocumentChange)
            .where(DocumentChange.document_id == document_id)
            .order_by(DocumentChange.created_at.desc())
            .limit(limit)
        )
        if severity is not None:
            stmt = stmt.where(DocumentChange.severity == severity)
        if change_type is not None:
            stmt = stmt.where(DocumentChange.change_type == change_type)
        if since is not None:
            stmt = stmt.where(DocumentChange.created_at >= since)
        if until is not None:
            stmt = stmt.where(DocumentChange.created_at <= until)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_change(self, document_id: uuid.UUID) -> DocumentChange | None:
        result = await self._session.execute(
            select(DocumentChange)
            .where(DocumentChange.document_id == document_id)
            .order_by(DocumentChange.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
