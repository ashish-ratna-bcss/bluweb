from __future__ import annotations

import difflib
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.intelligence_repository import IntelligenceRepository
from app.db.session import get_db
from app.schemas.document import (
    ChangeEventResponse,
    DiffResponse,
    DiffSummary,
    DocumentResponse,
    DocumentVersionResponse,
    DocumentVersionSummary,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    crawl_id: uuid.UUID | None = Query(default=None),
    source_id: uuid.UUID | None = Query(default=None),
    domain: str | None = Query(default=None),
    page_type: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentResponse]:
    """List documents. Prefer `source_id` for source-owned docs (monitoring).

    `crawl_id` remains for crawl-scoped handoff; `domain` is a coarse host
    filter. Clients must never require users to type these IDs — resolve
    them from selected Source / Crawl objects in the UI.
    """
    repo = DocumentRepository(db)
    documents = await repo.list_documents(
        crawl_job_id=crawl_id, source_id=source_id, domain=domain, page_type=page_type,
    )
    return [_to_document_response(d) for d in documents]


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> DocumentResponse:
    repo = DocumentRepository(db)
    document = await repo.get(document_id)
    if document is None:
        raise APIError(
            code="DOCUMENT_NOT_FOUND", message=f"No document found for id {document_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    latest_change = await repo.get_latest_change(document_id)
    intel_repo = IntelligenceRepository(db)
    entity_mentions = await intel_repo.get_document_entities(document_id)
    story_document = await intel_repo.get_story_document_for_document(document_id)
    return _to_document_response(
        document, latest_change,
        entity_ids=list({m.entity_id for m in entity_mentions}),
        story_id=story_document.story_id if story_document else None,
        story_match_confidence=story_document.confidence if story_document else None,
    )


@router.get("/{document_id}/changes", response_model=list[ChangeEventResponse])
async def list_document_changes(
    document_id: uuid.UUID,
    severity: str | None = Query(default=None),
    change_type: str | None = Query(default=None),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[ChangeEventResponse]:
    """Structured Phase 7 change history -- complements, not replaces, the
    existing `/diff` endpoint above: `/diff` computes a line diff between
    two version numbers on demand, this lists the stored ChangeResult for
    every meaningfully-changed crawl (change_type/severity/field diff)."""
    repo = DocumentRepository(db)
    if await repo.get(document_id) is None:
        raise APIError(
            code="DOCUMENT_NOT_FOUND", message=f"No document found for id {document_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    changes = await repo.list_changes(
        document_id, severity=severity, change_type=change_type,
        since=datetime.fromisoformat(from_) if from_ else None,
        until=datetime.fromisoformat(to) if to else None,
    )
    return [
        ChangeEventResponse(
            change_id=c.id, document_id=c.document_id, previous_version_id=c.previous_version_id,
            current_version_id=c.current_version_id, page_type=c.page_type, change_type=c.change_type,
            severity=c.severity, similarity=c.similarity, change_confidence=c.change_confidence,
            changed_fields=c.changed_fields, diff=c.diff, reasons=c.reasons, created_at=c.created_at,
        )
        for c in changes
    ]


@router.get("/{document_id}/versions", response_model=list[DocumentVersionSummary])
async def list_document_versions(
    document_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[DocumentVersionSummary]:
    repo = DocumentRepository(db)
    if await repo.get(document_id) is None:
        raise APIError(
            code="DOCUMENT_NOT_FOUND", message=f"No document found for id {document_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    versions = await repo.list_versions(document_id)
    return [DocumentVersionSummary.model_validate(v) for v in versions]


@router.get("/{document_id}/versions/{version_number}", response_model=DocumentVersionResponse)
async def get_document_version(
    document_id: uuid.UUID, version_number: int, db: AsyncSession = Depends(get_db)
) -> DocumentVersionResponse:
    repo = DocumentRepository(db)
    version = await repo.get_version(document_id, version_number)
    if version is None:
        raise APIError(
            code="VERSION_NOT_FOUND",
            message=f"No version {version_number} found for document {document_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return DocumentVersionResponse.model_validate(version)


@router.get("/{document_id}/diff", response_model=DiffResponse)
async def diff_document(
    document_id: uuid.UUID,
    from_version: int = Query(...),
    to_version: int = Query(...),
    db: AsyncSession = Depends(get_db),
) -> DiffResponse:
    repo = DocumentRepository(db)
    old = await repo.get_version(document_id, from_version)
    new = await repo.get_version(document_id, to_version)
    if old is None or new is None:
        raise APIError(
            code="VERSION_NOT_FOUND",
            message=f"Both versions {from_version} and {to_version} must exist for document {document_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    old_lines = old.content.splitlines()
    new_lines = new.content.splitlines()
    diff_lines = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))

    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

    return DiffResponse(
        document_id=document_id,
        from_version=from_version,
        to_version=to_version,
        changed=old.content_hash != new.content_hash,
        summary=DiffSummary(added_lines=added, removed_lines=removed),
        diff=diff_lines,
    )


def _to_document_response(
    document, latest_change=None, *, entity_ids=None, story_id=None, story_match_confidence=None,
) -> DocumentResponse:
    return DocumentResponse(
        document_id=document.id,
        url=document.url,
        canonical_url=document.canonical_url,
        domain=document.domain,
        title=document.title,
        author=document.author,
        published_at=document.published_at,
        language=document.language,
        content_type=document.content_type,
        page_type=document.page_type,
        extraction_method=document.extraction_method,
        extraction_confidence=document.extraction_confidence,
        current_version=document.current_version,
        collected_at=document.collected_at,
        latest_change_type=latest_change.change_type if latest_change else None,
        latest_change_severity=latest_change.severity if latest_change else None,
        latest_change_at=latest_change.created_at if latest_change else None,
        entity_ids=entity_ids or [],
        story_id=story_id,
        story_match_confidence=story_match_confidence,
    )
