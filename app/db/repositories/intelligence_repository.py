"""Phase 8 entity/story persistence.

Entity dedup (`get_or_create_entity_locked`) still uses the concurrency-safe
`INSERT ... ON CONFLICT DO NOTHING` + `SELECT ... FOR UPDATE` +
`populate_existing=True` pattern established (and twice debugged) in
crawl_repository.py/document_repository.py -- `entities` still has a real
partial unique index (`uq_webintel_entity_type_name`) in the unified schema.

Everything that used to be its own joinable child table --
`entity_aliases`, `entity_mentions`, `story_documents`, `story_entities` --
is now a JSONB list on the parent (`entity`/`document`/`story`) row, per
docs/unified_schema.sql. Repository methods here read-scan-append those
lists in Python instead of running a second-table INSERT/SELECT/JOIN. See
app/db/models/unified.py's docstring for the JSONB mutation rule (always
reassign a new list, never mutate in place).

Two of the old cross-table JOIN queries used to lack a GIN partner; the
schema now includes `ix_webintel_story_entities_gin` so
`get_entity_stories` / containment filters can use index-assisted
`@>` like `entity_mentions`. `find_candidate_stories` still bounds the
recency window in Python for candidate generation (intentional cheap
prefilter before scoring).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document
from app.db.models.intelligence import Entity, EntityMention, Story, StoryDocument, StoryEntity
from app.db.models.unified import WebIntelUnified

_CANDIDATE_SCAN_LIMIT = 500  # ponytail: bounded recency scan, see module docstring


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
        entity_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        insert_stmt = pg_insert(Entity).values(
            id=entity_id, record_kind="entity", entity_id=entity_id,
            entity_type=entity_type, canonical_name=canonical_name,
            normalized_name=normalized_name, entity_language=language, entity_confidence=confidence,
            first_seen_at=now, last_seen_at=now,
        ).on_conflict_do_nothing(
            index_elements=["entity_type", "normalized_name"],
            index_where=text(
                "record_kind = 'entity' AND entity_type IS NOT NULL AND normalized_name IS NOT NULL"
            ),
        )
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
        # Normalize both sides to UTC-aware to avoid naive/aware compare crashes
        # (Postgres may return aware timestamps while extractors pass naive).
        def _aware(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        seen = _aware(seen_at)
        if entity.first_seen_at is None:
            entity.first_seen_at = seen
        last = _aware(entity.last_seen_at) if entity.last_seen_at is not None else None
        if last is None or seen > last:
            entity.last_seen_at = seen

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
            select(Entity.id, Entity.entity_aliases).where(Entity.id.in_(entity_ids))
        )
        out: dict[uuid.UUID, list[str]] = {}
        for entity_id, aliases in result.all():
            out[entity_id] = [a["normalized_alias"] for a in (aliases or [])]
        return out

    async def add_alias_if_missing(
        self, *, entity_id: uuid.UUID, alias_text: str, normalized_alias: str, language: str | None, source: str, confidence: float,
    ) -> None:
        """No standalone unique constraint protects (entity_id,
        normalized_alias) anymore (it's a JSONB list entry, not its own
        row) -- lock the entity row first, something the old per-row
        `entity_aliases` table never needed."""
        result = await self._session.execute(
            select(Entity).where(Entity.id == entity_id).with_for_update().execution_options(populate_existing=True)
        )
        entity = result.scalar_one_or_none()
        if entity is None:
            return
        existing = entity.entity_aliases or []
        if any(a.get("normalized_alias") == normalized_alias for a in existing):
            return
        entry = {
            "id": str(uuid.uuid4()), "entity_id": str(entity_id), "alias_text": alias_text,
            "normalized_alias": normalized_alias, "language": language, "source": source,
            "confidence": confidence, "created_at": datetime.now(timezone.utc).isoformat(),
        }
        entity.entity_aliases = [*existing, entry]

    async def add_mention(self, **fields) -> EntityMention:
        document_id = fields["document_id"]
        entity_id = fields["entity_id"]
        start_offset = fields.get("start_offset", -1)

        result = await self._session.execute(
            select(Document).where(Document.id == document_id).with_for_update().execution_options(populate_existing=True)
        )
        document = result.scalar_one()
        existing = document.entity_mentions or []
        for entry in existing:
            if entry.get("entity_id") == str(entity_id) and entry.get("start_offset", -1) == start_offset:
                return _mention_from_entry(entry)

        mention_id = uuid.uuid4()
        entry = {
            "id": str(mention_id), "document_id": str(document_id), "entity_id": str(entity_id),
            "raw_text": fields["raw_text"], "normalized_text": fields["normalized_text"],
            "entity_type": fields["entity_type"], "confidence": fields["confidence"],
            "extractor": fields["extractor"], "start_offset": start_offset,
            "end_offset": fields.get("end_offset"), "context": fields.get("context"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        document.entity_mentions = [*existing, entry]
        return _mention_from_entry(entry)

    async def get_story_document_for_document(self, document_id: uuid.UUID) -> StoryDocument | None:
        """Used by the document detail API to expose story_id/confidence
        (spec section 36). No dedicated `story_documents` row/table exists
        to query by document_id alone anymore -- `attach_document_to_story`
        denormalizes the primary-story attachment onto the document row
        itself (`attached_story_id`/`story_match_*`), so this is a single
        PK lookup instead of a query over every story's JSONB list."""
        document = await self._session.execute(select(Document).where(Document.id == document_id))
        row = document.scalar_one_or_none()
        if row is None or row.attached_story_id is None:
            return None
        return StoryDocument(
            id=uuid.uuid4(), story_id=row.attached_story_id, document_id=document_id,
            match_score=row.story_match_score or 0.0, match_method=row.story_match_method or "",
            confidence=row.story_match_confidence or "", attached_at=row.updated_at,
        )

    async def get_document_entities(self, document_id: uuid.UUID) -> list[EntityMention]:
        result = await self._session.execute(select(Document).where(Document.id == document_id))
        document = result.scalar_one_or_none()
        if document is None:
            return []
        return [_mention_from_entry(entry) for entry in (document.entity_mentions or [])]

    async def get_entity_context_names(self, entity_id: uuid.UUID, *, limit: int = 50) -> list[str]:
        """Normalized names of entities that have previously co-occurred
        with `entity_id` in some document -- the corroboration signal
        entity_resolver.py's PERSON safety rule needs (spec section 33).
        Index-assisted via `entity_mentions`'s GIN containment index."""
        result = await self._session.execute(
            select(Document.entity_mentions).where(
                Document.entity_mentions.contains([{"entity_id": str(entity_id)}])
            )
        )
        other_ids: set[str] = set()
        for (mentions,) in result.all():
            for entry in mentions or []:
                other_id = entry.get("entity_id")
                if other_id and other_id != str(entity_id):
                    other_ids.add(other_id)
        if not other_ids:
            return []
        result = await self._session.execute(
            select(Entity.normalized_name).where(Entity.id.in_(other_ids)).distinct().limit(limit)
        )
        return [row[0] for row in result.all()]

    # -- stories --

    async def create_story(self, **fields) -> Story:
        story_id = uuid.uuid4()
        story = Story(id=story_id, story_id=story_id, **fields)
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
        entity-overlap, never an all-stories scan. Falls back to a
        recency-only window (no entity match required) when the document
        has no resolved entities at all, so genuinely entity-sparse pages
        still get *some* candidates to score against rather than always
        creating a new story. See module docstring re: no GIN index on
        `story_entities` -- the entity-overlap filter runs in Python over a
        bounded recency scan rather than a SQL join."""
        if entity_ids:
            entity_id_strs = {str(e) for e in entity_ids}
            result = await self._session.execute(
                select(Story)
                .where(Story.last_activity_at >= since)
                .order_by(Story.last_activity_at.desc())
                .limit(_CANDIDATE_SCAN_LIMIT)
            )
            recent = list(result.scalars().all())
            candidates = [
                story for story in recent
                if any(e.get("entity_id") in entity_id_strs for e in (story.story_entities or []))
            ][:limit]
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
        # `story` may not be locked by the caller (story_service.py doesn't
        # call get_story_locked before this) -- (re-)lock by id here, since
        # `story_documents`/`story_entities` are JSONB lists with no
        # standalone unique constraint to fall back on anymore.
        locked = await self.get_story_locked(story.id)
        existing_entries = locked.story_documents or []
        entry = next((e for e in existing_entries if e.get("document_id") == str(document_id)), None)
        if entry is None:
            entry = {
                "id": str(uuid.uuid4()), "story_id": str(locked.id), "document_id": str(document_id),
                "match_score": match_score, "match_method": match_method, "confidence": confidence,
                "feature_scores": feature_scores, "matching_evidence": matching_evidence,
                "scoring_version": scoring_version, "attached_at": datetime.now(timezone.utc).isoformat(),
                "domain": document_domain,
                "published_at": document_published_at.isoformat() if document_published_at else None,
            }
            locked.story_documents = [*existing_entries, entry]

        self._recompute_story_aggregates(locked)
        self._upsert_story_entities(locked, document_entity_ids)

        # Denormalized primary-story attachment (see get_story_document_for_document).
        # Lock the document row -- attached_story_* is a concurrent write target
        # when two crawls attach different docs or re-attach the same doc.
        doc_result = await self._session.execute(
            select(Document)
            .where(Document.id == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        document = doc_result.scalar_one_or_none()
        if document is not None:
            document.attached_story_id = locked.id
            document.story_match_score = match_score
            document.story_match_method = match_method
            document.story_match_confidence = confidence

        await self._session.commit()
        return _story_document_from_entry(entry)

    def _recompute_story_aggregates(self, story: Story) -> None:
        """Full recompute over the (now-updated) `story_documents` JSONB
        list -- no join needed since each entry already carries the
        attached document's domain/published_at (enriched at attach time,
        see `attach_document_to_story`)."""
        def _aware(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        entries = story.story_documents or []
        story.document_count = len(entries)
        story.source_count = len({e["domain"] for e in entries if e.get("domain")})
        story.last_activity_at = datetime.now(timezone.utc)

        published_ats = [
            _aware(datetime.fromisoformat(e["published_at"]))
            for e in entries
            if e.get("published_at")
        ]
        if published_ats:
            earliest, latest = min(published_ats), max(published_ats)
            first = _aware(story.first_published_at) if story.first_published_at else None
            last = _aware(story.last_published_at) if story.last_published_at else None
            if first is None or earliest < first:
                story.first_published_at = earliest
            if last is None or latest > last:
                story.last_published_at = latest

    def _upsert_story_entities(self, story: Story, entity_ids: list[uuid.UUID]) -> None:
        entries = {e["entity_id"]: dict(e) for e in (story.story_entities or [])}
        now_iso = datetime.now(timezone.utc).isoformat()
        for entity_id in entity_ids:
            key = str(entity_id)
            if key in entries:
                entries[key]["mention_count"] = entries[key].get("mention_count", 1) + 1
                entries[key]["last_seen_at"] = now_iso
            else:
                entries[key] = {
                    "id": str(uuid.uuid4()), "story_id": str(story.id), "entity_id": key,
                    "mention_count": 1, "first_seen_at": now_iso, "last_seen_at": now_iso, "importance": 0.5,
                }
        story.story_entities = list(entries.values())
        story.entity_count = len(entries)

    async def get_story_documents(self, story_id: uuid.UUID) -> list[StoryDocument]:
        story = await self.get_story(story_id)
        if story is None:
            return []
        entries = sorted(story.story_documents or [], key=lambda e: e.get("attached_at") or "")
        return [_story_document_from_entry(e) for e in entries]

    async def get_story_entities(self, story_id: uuid.UUID) -> list[StoryEntity]:
        story = await self.get_story(story_id)
        if story is None:
            return []
        entries = sorted(story.story_entities or [], key=lambda e: e.get("mention_count", 0), reverse=True)
        return [_story_entity_from_entry(e) for e in entries]

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
            select(Document.id)
            .where(Document.entity_mentions.contains([{"entity_id": str(entity_id)}]))
            .limit(limit)
        )
        return [row[0] for row in result.all()]

    async def get_entity_stories(self, entity_id: uuid.UUID, *, limit: int = 100) -> list[Story]:
        result = await self._session.execute(
            select(Story)
            .where(Story.story_entities.contains([{"entity_id": str(entity_id)}]))
            .order_by(Story.last_activity_at.desc())
            .limit(limit)
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
        story = await self.get_story(story_id)
        if story is None:
            return []
        entries = [_story_document_from_entry(e) for e in (story.story_documents or [])]
        entries.sort(key=lambda d: d.published_at or d.attached_at or datetime.min.replace(tzinfo=timezone.utc))
        return entries

    async def get_story_sources(self, story_id: uuid.UUID) -> list[tuple[str, int, datetime | None, datetime | None]]:
        """(domain, document_count, first_published_at, last_published_at)
        per source -- derived from the story's own `story_documents` JSONB
        list (enriched with domain/published_at at attach time), no join."""
        story = await self.get_story(story_id)
        if story is None:
            return []
        by_domain: dict[str, list[datetime | None]] = {}
        for entry in story.story_documents or []:
            domain = entry.get("domain")
            if not domain:
                continue
            published_at = datetime.fromisoformat(entry["published_at"]) if entry.get("published_at") else None
            by_domain.setdefault(domain, []).append(published_at)

        out = []
        for domain, published_ats in by_domain.items():
            dated = [p for p in published_ats if p is not None]
            out.append((domain, len(published_ats), min(dated) if dated else None, max(dated) if dated else None))
        return out


def _mention_from_entry(entry: dict) -> EntityMention:
    return EntityMention(
        id=uuid.UUID(entry["id"]), document_id=uuid.UUID(entry["document_id"]), entity_id=uuid.UUID(entry["entity_id"]),
        raw_text=entry["raw_text"], normalized_text=entry["normalized_text"], entity_type=entry["entity_type"],
        confidence=entry["confidence"], extractor=entry["extractor"], start_offset=entry.get("start_offset", -1),
        end_offset=entry.get("end_offset"), context=entry.get("context"),
        created_at=datetime.fromisoformat(entry["created_at"]) if entry.get("created_at") else None,
    )


def _story_document_from_entry(entry: dict) -> StoryDocument:
    return StoryDocument(
        id=uuid.UUID(entry["id"]), story_id=uuid.UUID(entry["story_id"]), document_id=uuid.UUID(entry["document_id"]),
        match_score=entry["match_score"], match_method=entry["match_method"], confidence=entry["confidence"],
        feature_scores=entry.get("feature_scores", {}), matching_evidence=entry.get("matching_evidence", []),
        scoring_version=entry.get("scoring_version", "v1"),
        attached_at=datetime.fromisoformat(entry["attached_at"]) if entry.get("attached_at") else None,
        domain=entry.get("domain"),
        published_at=datetime.fromisoformat(entry["published_at"]) if entry.get("published_at") else None,
    )


def _story_entity_from_entry(entry: dict) -> StoryEntity:
    return StoryEntity(
        id=uuid.UUID(entry["id"]), story_id=uuid.UUID(entry["story_id"]), entity_id=uuid.UUID(entry["entity_id"]),
        mention_count=entry.get("mention_count", 1),
        first_seen_at=datetime.fromisoformat(entry["first_seen_at"]) if entry.get("first_seen_at") else None,
        last_seen_at=datetime.fromisoformat(entry["last_seen_at"]) if entry.get("last_seen_at") else None,
        importance=entry.get("importance", 0.5),
    )
