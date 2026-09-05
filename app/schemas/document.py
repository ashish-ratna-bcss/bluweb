from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    document_id: UUID
    url: str
    canonical_url: str | None
    domain: str
    title: str | None
    author: str | None
    published_at: datetime | None
    language: str | None
    content_type: str
    page_type: str | None
    extraction_method: str
    extraction_confidence: float
    current_version: int
    collected_at: datetime
    latest_change_type: str | None = None
    latest_change_severity: str | None = None
    latest_change_at: datetime | None = None
    entity_ids: list[UUID] = []
    story_id: UUID | None = None
    story_match_confidence: str | None = None

    model_config = {"from_attributes": True}


class ChangeEventResponse(BaseModel):
    """Detail shape -- includes the full diff. Deliberately not embedded in
    DocumentResponse/list endpoints (spec section 43: don't expose huge
    diffs in list endpoints)."""

    change_id: UUID
    document_id: UUID
    previous_version_id: UUID | None
    current_version_id: UUID | None
    page_type: str | None
    change_type: str
    severity: str
    similarity: float
    change_confidence: float
    changed_fields: list[str]
    diff: dict
    reasons: list[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentVersionResponse(BaseModel):
    version_number: int
    change_type: str
    title: str | None
    content: str
    content_hash: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentVersionSummary(BaseModel):
    version_number: int
    change_type: str
    content_hash: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DiffSummary(BaseModel):
    added_lines: int
    removed_lines: int


class DiffResponse(BaseModel):
    document_id: UUID
    from_version: int
    to_version: int
    changed: bool
    summary: DiffSummary
    diff: list[str]
