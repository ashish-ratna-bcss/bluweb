from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class EntityResponse(BaseModel):
    entity_id: UUID
    entity_type: str
    canonical_name: str
    normalized_name: str
    language: str | None
    confidence: float
    first_seen_at: datetime
    last_seen_at: datetime

    model_config = {"from_attributes": True}


class EntityDocumentsResponse(BaseModel):
    entity_id: UUID
    document_ids: list[UUID]


class EntityStorySummary(BaseModel):
    story_id: UUID
    canonical_title: str | None
    status: str
    document_count: int


class EntityStoriesResponse(BaseModel):
    entity_id: UUID
    stories: list[EntityStorySummary]


class StorySummaryResponse(BaseModel):
    story_id: UUID
    canonical_title: str | None
    status: str
    first_seen_at: datetime
    last_activity_at: datetime
    document_count: int
    source_count: int
    entity_count: int

    model_config = {"from_attributes": True}


class StoryDocumentResponse(BaseModel):
    document_id: UUID
    match_score: float
    match_method: str
    confidence: str
    feature_scores: dict
    matching_evidence: list
    scoring_version: str
    attached_at: datetime

    model_config = {"from_attributes": True}


class StoryEntityResponse(BaseModel):
    entity_id: UUID
    entity_type: str
    canonical_name: str
    mention_count: int
    importance: float


class StorySourceResponse(BaseModel):
    domain: str
    document_count: int
    first_published_at: datetime | None
    last_published_at: datetime | None


class StoryTimelineEntry(BaseModel):
    document_id: UUID
    domain: str
    timestamp: datetime | None
    match_method: str
    confidence: str


class ExplainDecision(BaseModel):
    """spec section 40's own JSON shape -- reused verbatim as the API response."""

    decision: str
    score: float
    confidence: str
    reasons: list[str]
    features: dict
    scoring_version: str
