from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.db.repositories.intelligence_repository import IntelligenceRepository
from app.db.session import get_db
from app.schemas.intelligence import (
    EntityDocumentsResponse,
    EntityResponse,
    EntityStorySummary,
    EntityStoriesResponse,
    StoryDocumentResponse,
    StoryEntityResponse,
    StorySourceResponse,
    StorySummaryResponse,
    StoryTimelineEntry,
)

router = APIRouter(tags=["intelligence"])


def _to_entity_response(entity) -> EntityResponse:
    return EntityResponse(
        entity_id=entity.id, entity_type=entity.entity_type, canonical_name=entity.canonical_name,
        normalized_name=entity.normalized_name, language=entity.language, confidence=entity.confidence,
        first_seen_at=entity.first_seen_at, last_seen_at=entity.last_seen_at,
    )


@router.get("/entities", response_model=list[EntityResponse])
async def list_entities(
    entity_type: str | None = Query(default=None), db: AsyncSession = Depends(get_db),
) -> list[EntityResponse]:
    repo = IntelligenceRepository(db)
    entities = await repo.list_entities(entity_type=entity_type)
    return [_to_entity_response(e) for e in entities]


@router.get("/entities/{entity_id}", response_model=EntityResponse)
async def get_entity(entity_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> EntityResponse:
    repo = IntelligenceRepository(db)
    entity = await repo.get_entity(entity_id)
    if entity is None:
        raise APIError(code="ENTITY_NOT_FOUND", message=f"No entity found for id {entity_id}", status_code=status.HTTP_404_NOT_FOUND)
    return _to_entity_response(entity)


@router.get("/entities/{entity_id}/documents", response_model=EntityDocumentsResponse)
async def get_entity_documents(entity_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> EntityDocumentsResponse:
    repo = IntelligenceRepository(db)
    if await repo.get_entity(entity_id) is None:
        raise APIError(code="ENTITY_NOT_FOUND", message=f"No entity found for id {entity_id}", status_code=status.HTTP_404_NOT_FOUND)
    document_ids = await repo.get_entity_documents(entity_id)
    return EntityDocumentsResponse(entity_id=entity_id, document_ids=document_ids)


@router.get("/entities/{entity_id}/stories", response_model=EntityStoriesResponse)
async def get_entity_stories(entity_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> EntityStoriesResponse:
    repo = IntelligenceRepository(db)
    if await repo.get_entity(entity_id) is None:
        raise APIError(code="ENTITY_NOT_FOUND", message=f"No entity found for id {entity_id}", status_code=status.HTTP_404_NOT_FOUND)
    stories = await repo.get_entity_stories(entity_id)
    return EntityStoriesResponse(
        entity_id=entity_id,
        stories=[
            EntityStorySummary(story_id=s.id, canonical_title=s.canonical_title, status=s.status, document_count=s.document_count)
            for s in stories
        ],
    )


def _to_story_response(story) -> StorySummaryResponse:
    # Counts are nullable on the STI row until the first recompute; API contract
    # requires ints (StorySummaryResponse), so coerce None → 0 for list/detail.
    return StorySummaryResponse(
        story_id=story.id,
        canonical_title=story.canonical_title,
        status=story.status or "unknown",
        first_seen_at=story.first_seen_at,
        last_activity_at=story.last_activity_at,
        document_count=int(story.document_count or 0),
        source_count=int(story.source_count or 0),
        entity_count=int(story.entity_count or 0),
    )


@router.get("/stories", response_model=list[StorySummaryResponse])
async def list_stories(
    status_filter: str | None = Query(default=None, alias="status"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[StorySummaryResponse]:
    repo = IntelligenceRepository(db)
    stories = await repo.list_stories(status=status_filter, from_time=from_, to_time=to)
    return [_to_story_response(s) for s in stories]


@router.get("/stories/{story_id}", response_model=StorySummaryResponse)
async def get_story(story_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> StorySummaryResponse:
    repo = IntelligenceRepository(db)
    story = await repo.get_story(story_id)
    if story is None:
        raise APIError(code="STORY_NOT_FOUND", message=f"No story found for id {story_id}", status_code=status.HTTP_404_NOT_FOUND)
    return _to_story_response(story)


@router.get("/stories/{story_id}/documents", response_model=list[StoryDocumentResponse])
async def get_story_documents(story_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[StoryDocumentResponse]:
    repo = IntelligenceRepository(db)
    if await repo.get_story(story_id) is None:
        raise APIError(code="STORY_NOT_FOUND", message=f"No story found for id {story_id}", status_code=status.HTTP_404_NOT_FOUND)
    rows = await repo.get_story_documents(story_id)
    return [StoryDocumentResponse.model_validate(r) for r in rows]


@router.get("/stories/{story_id}/entities", response_model=list[StoryEntityResponse])
async def get_story_entities(story_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[StoryEntityResponse]:
    repo = IntelligenceRepository(db)
    if await repo.get_story(story_id) is None:
        raise APIError(code="STORY_NOT_FOUND", message=f"No story found for id {story_id}", status_code=status.HTTP_404_NOT_FOUND)
    rows = await repo.get_story_entities(story_id)
    out = []
    for row in rows:
        entity = await repo.get_entity(row.entity_id)
        if entity is not None:
            out.append(StoryEntityResponse(
                entity_id=entity.id, entity_type=entity.entity_type, canonical_name=entity.canonical_name,
                mention_count=row.mention_count, importance=row.importance,
            ))
    return out


@router.get("/stories/{story_id}/sources", response_model=list[StorySourceResponse])
async def get_story_sources(story_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[StorySourceResponse]:
    repo = IntelligenceRepository(db)
    if await repo.get_story(story_id) is None:
        raise APIError(code="STORY_NOT_FOUND", message=f"No story found for id {story_id}", status_code=status.HTTP_404_NOT_FOUND)
    rows = await repo.get_story_sources(story_id)
    return [
        StorySourceResponse(domain=domain, document_count=count, first_published_at=first, last_published_at=last)
        for domain, count, first, last in rows
    ]


@router.get("/stories/{story_id}/timeline", response_model=list[StoryTimelineEntry])
async def get_story_timeline(story_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[StoryTimelineEntry]:
    """Spec section 27: reconstruct the story's timeline. Ordered by
    document published_at (falling back to attach time for documents
    without one) -- never crawl time, per the spec's own warning not to
    confuse the two."""
    repo = IntelligenceRepository(db)
    if await repo.get_story(story_id) is None:
        raise APIError(code="STORY_NOT_FOUND", message=f"No story found for id {story_id}", status_code=status.HTTP_404_NOT_FOUND)

    from app.db.repositories.document_repository import DocumentRepository
    doc_repo = DocumentRepository(db)
    rows = await repo.get_story_timeline(story_id)
    entries = []
    for row in rows:
        document = await doc_repo.get(row.document_id)
        if document is None:
            continue
        entries.append(StoryTimelineEntry(
            document_id=document.id, domain=document.domain,
            timestamp=document.published_at or row.attached_at,
            match_method=row.match_method, confidence=row.confidence,
        ))
    return entries
