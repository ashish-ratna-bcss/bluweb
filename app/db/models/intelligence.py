"""Phase 8 entity/story intelligence models.

Deliberately minimal relative to the spec's full object list (section 15):
no separate `document_entities` table (`EntityMention` already carries the
document<->entity relationship with mention-level detail -- a document_id/
entity_id pair with no mention detail would be redundant with it), no
`story_sources` table (source diversity -- domain list, first/latest
source -- is a derived query over `story_documents` joined to `documents`,
same "don't create a table a query already answers" discipline Phase 7's
`document_changes` design used), no separate `Event`/`EventEntity`/
`EventDocument` tables (an "event" is a derived view over a story's
entities + earliest timestamp + representative document, not a modeled
object yet -- see the Phase 8 research report, section 11).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (
        # Protects the exact-name-match resolution path (entity_resolver.py)
        # from concurrent-duplicate races the same way Phase 6/7 protect
        # FetchStrategyStats/Document: INSERT...ON CONFLICT DO NOTHING +
        # SELECT...FOR UPDATE keyed on this constraint (intelligence_repository.py).
        # Fuzzy/alias-match resolution can't use this same guarantee (a
        # fuzzy variant has a different normalized_name by definition) --
        # documented as a known, bounded limitation in the Phase 8 report.
        UniqueConstraint("entity_type", "normalized_name", name="uq_entity_type_normalized_name"),
        Index("ix_entities_normalized_name", "normalized_name"),
        Index(
            "ix_entities_normalized_name_trgm", "normalized_name",
            postgresql_using="gin", postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class EntityAlias(Base):
    __tablename__ = "entity_aliases"
    __table_args__ = (
        UniqueConstraint("entity_id", "normalized_alias", name="uq_entity_alias_entity_normalized"),
        Index("ix_entity_aliases_normalized_alias", "normalized_alias"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # "extraction" | "merge"
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class EntityMention(Base):
    __tablename__ = "entity_mentions"
    __table_args__ = (
        Index("ix_entity_mentions_document_id", "document_id"),
        Index("ix_entity_mentions_entity_id", "entity_id"),
        UniqueConstraint("document_id", "entity_id", "start_offset", name="uq_entity_mention_doc_entity_offset"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    extractor: Mapped[str] = mapped_column(String(32), nullable=False)  # "regex" | "spacy" | "gliner" | "indicner"
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)  # -1 = offset not tracked
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class Story(Base):
    __tablename__ = "stories"
    __table_args__ = (
        Index("ix_stories_last_activity_at", "last_activity_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    representative_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE", index=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    first_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    scoring_version: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class StoryDocument(Base):
    __tablename__ = "story_documents"
    __table_args__ = (
        UniqueConstraint("story_id", "document_id", name="uq_story_document"),
        Index("ix_story_documents_story_id", "story_id"),
        Index("ix_story_documents_document_id", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    story_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stories.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    match_method: Mapped[str] = mapped_column(String(32), nullable=False)  # "seed" | "scored"
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)  # HIGH | MEDIUM | LOW
    feature_scores: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    matching_evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    scoring_version: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")
    attached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class StoryEntity(Base):
    __tablename__ = "story_entities"
    __table_args__ = (
        UniqueConstraint("story_id", "entity_id", name="uq_story_entity"),
        Index("ix_story_entities_story_id", "story_id"),
        Index("ix_story_entities_entity_id", "entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    story_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stories.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
