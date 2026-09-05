import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Computed, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RawArtifact(Base):
    __tablename__ = "raw_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    crawl_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False, default="html")
    page_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    # Denormalized copy of the latest version's text, kept for full-text
    # search without joining document_versions on every query. This is
    # exactly the seam SearchRepository (app/services/search/) exists for:
    # swap the Postgres to_tsvector query for an OpenSearch index later
    # without changing the documents table or callers.
    current_content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Postgres-native stored generated column (spec Phase Q): computed by
    # the database itself from title+current_content whenever either
    # changes, with a GIN index below -- replaces PostgresSearchRepository's
    # old query-time `to_tsvector(...)` (recomputed per row per query) with
    # an index lookup. SearchRepository stays the abstraction either way;
    # this only changes what's behind it.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(title, '') || ' ' || current_content)", persisted=True),
        nullable=True,
    )

    extracted_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    links: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    images: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    normalized_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    simhash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False, default="trafilatura")
    extraction_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    change_type: Mapped[str] = mapped_column(String(16), nullable=False)  # NEW | UPDATED

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    raw_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_artifacts.id", ondelete="SET NULL"), nullable=True
    )
    extraction_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class DocumentChange(Base):
    """Phase 7 structured change event: one row per meaningfully-changed
    crawl (not one per fetch -- crawl_engine.py only writes this when
    ChangeDetectionService reports `changed=True`). Deliberately separate
    from DocumentVersion: that table stays the authoritative NEW|UPDATED
    version history (unchanged from Phase 1-6); this table adds the
    field-level *why* for the UPDATED versions without overloading its
    existing coarse `change_type` column ("NEW"/"UPDATED") with the much
    finer Phase 7 taxonomy (PRICE_CHANGED, NEW_POSTS, ...)."""

    __tablename__ = "document_changes"
    __table_args__ = (
        Index("ix_document_changes_document_created", "document_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )

    page_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    similarity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    change_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    changed_fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    diff: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, index=True)
