"""Phase 8 entity/story persistence. Same concurrency-safe get-or-create
pattern established (and twice debugged) in Phase 6/7's crawl_repository.py
and document_repository.py: `INSERT ... ON CONFLICT DO NOTHING` +
`SELECT ... FOR UPDATE` + `populate_existing=True` for any row a document-
processing task might read once (for candidate lookup) and then write
again later in the same session -- the exact identity-map staleness class
of bug this codebase has hit before.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document
from app.db.models.intelligence import Entity, EntityAlias, EntityMention, Story, StoryDocument, StoryEntity


class IntelligenceRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    # -- entities --

    async def get_or_create_entity_locked(
        self, *, entity_type: str, canonical_name: str, normalized_name: str, language: str | None, confidence: float,
    ) -> tuple[Entity, bool]:
        """Returns (entity, was_created). Concurrency-safe: two tasks
        resolving the same (entity_type, normalized_name) concurrently
        never create two rows (spec section 44/45 -- idempotent, no
        duplicate entities under concurrent processing)."""
        insert_stmt = pg_insert(Entity).values(
            id=uuid.uuid4(), entity_type=entity_type, canonical_name=canonical_name,
            normalized_name=normalized_name, language=language, confidence=confidence,
        ).on_conflict_do_nothing(index_elements=["entity_type", "normalized_name"])
        result = await self._session.execute(insert_stmt.returning(Entity.id))
        created_id = result.scalar_one_or_none()

        result = await self._session.execute(
            select(Entity)
            .where(Entity.entity_type == entity_type, Entity.normalized_name == normalized_name)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        entity = result.scalar_one()
        return entity, created_id is not None

    async def touch_entity_last_seen(self, entity: Entity, *, seen_at: datetime) -> None:
        if seen_at > entity.last_seen_at:
            entity.last_seen_at = seen_at

    async def get_entity(self, entity_id: uuid.UUID) -> Entity | None:
        result = await self._session.execute(select(Entity).where(Entity.id == entity_id))
        return result.scalar_one_or_none()

    async def find_entities_by_type(self, entity_type: str, *, limit: int = 200) -> list[Entity]:
        result = await self._session.execute(
            select(Entity).where(Entity.entity_type == entity_type).limit(limit)
        )
        return list(result.scalars().all())

    async def get_aliases_for_entities(self, entity_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        if not entity_ids:
            return {}
        result = await self._session.execute(
            select(EntityAlias.entity_id, EntityAlias.normalized_alias).where(EntityAlias.entity_id.in_(entity_ids))
        )
        out: dict[uuid.UUID, list[str]] = {}
        for entity_id, alias in result.all():
            out.setdefault(entity_id, []).append(alias)
        return out

    async def add_alias_if_missing(
        self, *, entity_id: uuid.UUID, alias_text: str, normalized_alias: str, language: str | None, source: str, confidence: float,
    ) -> None:
        insert_stmt = pg_insert(EntityAlias).values(
            id=uuid.uuid4(), entity_id=entity_id, alias_text=alias_text, normalized_alias=normalized_alias,
            language=language, source=source, confidence=confidence,
        ).on_conflict_do_nothing(index_elements=["entity_id", "normalized_alias"])
        await self._session.execute(insert_stmt)

    async def add_mention(self, **fields) -> EntityMention:
        insert_stmt = pg_insert(EntityMention).values(id=uuid.uuid4(), **fields).on_conflict_do_nothing(
            index_elements=["document_id", "entity_id", "start_offset"]
        )
        await self._session.execute(insert_stmt)
        result = await self._session.execute(
            select(EntityMention).where(
                EntityMention.document_id == fields["document_id"],
                EntityMention.entity_id == fields["entity_id"],
                EntityMention.start_offset == fields["start_offset"],
            )
        )
        return result.scalar_one()

    async def get_story_document_for_document(self, document_id: uuid.UUID) -> StoryDocument | None:
        """Used by the document detail API to expose story_id/confidence
        (spec section 36) without a large nested payload -- one row, not a
        joined story object."""
        result = await self._session.execute(
            select(StoryDocument).where(StoryDocument.document_id == document_id).order_by(StoryDocument.attached_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_document_entities(self, document_id: uuid.UUID) -> list[EntityMention]:
        result = await self._session.execute(
            select(EntityMention).where(EntityMention.document_id == document_id)
        )
        return list(result.scalars().all())

    async def get_entity_context_names(self, entity_id: uuid.UUID, *, limit: int = 50) -> list[str]:
        """Normalized names of entities that have previously co-occurred
        with `entity_id` in some document -- the corroboration signal
        entity_resolver.py's PERSON safety rule needs (spec section 33)."""
        other_mentions = select(EntityMention.document_id).where(EntityMention.entity_id == entity_id).subquery()
        result = await self._session.execute(
            select(Entity.normalized_name)
            .join(EntityMention, EntityMention.entity_id == Entity.id)
            .where(EntityMention.document_id.in_(select(other_mentions)), Entity.id != entity_id)
            .distinct()
            .limit(limit)
        )
        return [row[0] for row in result.all()]

    # -- stories --

    async def create_story(self, **fields) -> Story:
        story = Story(id=uuid.uuid4(), **fields)
        self._session.add(story)
        await self._session.flush()
        return story

    async def get_story_locked(self, story_id: uuid.UUID) -> Story:
        result = await self._session.execute(
            select(Story).where(Story.id == story_id).with_for_update().execution_options(populate_existing=True)
        )
        return result.scalar_one()

    async def get_story(self, story_id: uuid.UUID) -> Story | None:
        result = await self._session.execute(select(Story).where(Story.id == story_id))
        return result.scalar_one_or_none()

    async def find_candidate_stories(
        self, *, entity_ids: list[uuid.UUID], since: datetime, limit: int = 20,
    ) -> list[Story]:
        """Cheap candidate generation (spec section 19): time-windowed
        entity-overlap query, never an all-stories scan. Falls back to a
        recency-only window (no entity match required) when the document
        has no resolved entities at all, so genuinely entity-sparse pages
        (e.g. a bare classified listing) still get *some* candidates to
        score against rather than always creating a new story."""
        if entity_ids:
            result = await self._session.execute(
                select(Story)
                .join(StoryEntity, StoryEntity.story_id == Story.id)
                .where(StoryEntity.entity_id.in_(entity_ids), Story.last_activity_at >= since)
                .distinct()
                .order_by(Story.last_activity_at.desc())
                .limit(limit)
            )
            candidates = list(result.scalars().all())
            if candidates:
                return candidates

        result = await self._session.execute(
            select(Story).where(Story.last_activity_at >= since).order_by(Story.last_activity_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def attach_document_to_story(
        self, *, story: Story, document_id: uuid.UUID, match_score: float, match_method: str,
        confidence: str, feature_scores: dict, matching_evidence: list, scoring_version: str,
        document_domain: str, document_published_at: datetime | None, document_entity_ids: list[uuid.UUID],
    ) -> StoryDocument:
        insert_stmt = pg_insert(StoryDocument).values(
            id=uuid.uuid4(), story_id=story.id, document_id=document_id, match_score=match_score,
            match_method=match_method, confidence=confidence, feature_scores=feature_scores,
            matching_evidence=matching_evidence, scoring_version=scoring_version,
        ).on_conflict_do_nothing(index_elements=["story_id", "document_id"])
        await self._session.execute(insert_stmt)

        result = await self._session.execute(
            select(StoryDocument).where(StoryDocument.story_id == story.id, StoryDocument.document_id == document_id)
        )
        story_document = result.scalar_one()

        await self._recompute_story_aggregates(story, document_published_at=document_published_at)
        await self._upsert_story_entities(story.id, document_entity_ids)
        return story_document

    async def _recompute_story_aggregates(self, story: Story, *, document_published_at: datetime | None) -> None:
        doc_count_result = await self._session.execute(
            select(func.count()).select_from(StoryDocument).where(StoryDocument.story_id == story.id)
        )
        story.document_count = doc_count_result.scalar_one()

        domain_count_result = await self._session.execute(
            select(func.count(func.distinct(Document.domain)))
            .select_from(StoryDocument)
            .join(Document, Document.id == StoryDocument.document_id)
            .where(StoryDocument.story_id == story.id)
        )
        story.source_count = domain_count_result.scalar_one()

        story.last_activity_at = datetime.now(timezone.utc)
        if document_published_at is not None:
            if story.first_published_at is None or document_published_at < story.first_published_at:
                story.first_published_at = document_published_at
            if story.last_published_at is None or document_published_at > story.last_published_at:
                story.last_published_at = document_published_at

    async def _upsert_story_entities(self, story_id: uuid.UUID, entity_ids: list[uuid.UUID]) -> None:
        for entity_id in entity_ids:
            insert_stmt = pg_insert(StoryEntity).values(
                id=uuid.uuid4(), story_id=story_id, entity_id=entity_id, mention_count=1,
            ).on_conflict_do_update(
                index_elements=["story_id", "entity_id"],
                set_={"mention_count": StoryEntity.mention_count + 1, "last_seen_at": func.now()},
            )
            await self._session.execute(insert_stmt)

        count_result = await self._session.execute(
            select(func.count()).select_from(StoryEntity).where(StoryEntity.story_id == story_id)
        )
        story = await self.get_story(story_id)
        if story is not None:
            story.entity_count = count_result.scalar_one()

    async def get_story_documents(self, story_id: uuid.UUID) -> list[StoryDocument]:
        result = await self._session.execute(
            select(StoryDocument).where(StoryDocument.story_id == story_id).order_by(StoryDocument.attached_at)
        )
        return list(result.scalars().all())

    async def get_story_entities(self, story_id: uuid.UUID) -> list[StoryEntity]:
        result = await self._session.execute(
            select(StoryEntity).where(StoryEntity.story_id == story_id).order_by(StoryEntity.mention_count.desc())
        )
        return list(result.scalars().all())

    # -- API-facing list/filter queries (spec section 35) --

    async def list_entities(
        self, *, entity_type: str | None = None, limit: int = 100,
    ) -> list[Entity]:
        stmt = select(Entity).order_by(Entity.last_seen_at.desc()).limit(limit)
        if entity_type is not None:
            stmt = stmt.where(Entity.entity_type == entity_type)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_entity_documents(self, entity_id: uuid.UUID, *, limit: int = 100) -> list[uuid.UUID]:
        result = await self._session.execute(
            select(EntityMention.document_id).where(EntityMention.entity_id == entity_id).distinct().limit(limit)
        )
        return [row[0] for row in result.all()]

    async def get_entity_stories(self, entity_id: uuid.UUID, *, limit: int = 100) -> list[Story]:
        result = await self._session.execute(
            select(Story).join(StoryEntity, StoryEntity.story_id == Story.id)
            .where(StoryEntity.entity_id == entity_id).order_by(Story.last_activity_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def list_stories(
        self, *, status: str | None = None, from_time: datetime | None = None, to_time: datetime | None = None,
        limit: int = 100,
    ) -> list[Story]:
        stmt = select(Story).order_by(Story.last_activity_at.desc()).limit(limit)
        if status is not None:
            stmt = stmt.where(Story.status == status)
        if from_time is not None:
            stmt = stmt.where(Story.last_activity_at >= from_time)
        if to_time is not None:
            stmt = stmt.where(Story.last_activity_at <= to_time)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_story_timeline(self, story_id: uuid.UUID) -> list[StoryDocument]:
        result = await self._session.execute(
            select(StoryDocument)
            .join(Document, Document.id == StoryDocument.document_id)
            .where(StoryDocument.story_id == story_id)
            .order_by(func.coalesce(Document.published_at, StoryDocument.attached_at))
        )
        return list(result.scalars().all())

    async def get_story_sources(self, story_id: uuid.UUID) -> list[tuple[str, int, datetime | None, datetime | None]]:
        """(domain, document_count, first_published_at, last_published_at)
        per source -- derived via a join, no separate story_sources table
        (spec section 26: don't create a table a query already answers)."""
        result = await self._session.execute(
            select(
                Document.domain, func.count(), func.min(Document.published_at), func.max(Document.published_at),
            )
            .select_from(StoryDocument)
            .join(Document, Document.id == StoryDocument.document_id)
            .where(StoryDocument.story_id == story_id)
            .group_by(Document.domain)
        )
        return list(result.all())
