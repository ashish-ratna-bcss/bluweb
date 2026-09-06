"""Single-table-inheritance root for `webintel_unified`.

This ORM layer replaced the old normalized 18-table schema (migrations/
versions/, formerly separate model files per table) so the app can run
against a single, already-provisioned Postgres table on an external server
(schema: docs/unified_schema.sql, applied out-of-band -- Alembic is no
longer used to manage this schema, see migrations/env.py).

`webintel_unified` is a discriminated-union table: one physical table, one
`record_kind` column, 9 logical row kinds. This root class maps every
column once; `app/db/models/{document,crawl,intelligence,source,
preflight}.py` declare one SQLAlchemy single-table-inheritance subclass per
`record_kind` (`polymorphic_identity=...`), which makes SQLAlchemy
auto-inject `WHERE record_kind = '...'` into every `select(Document)`,
`select(Entity)`, etc. -- required because the DDL's own uniqueness/lookup
indexes are all partial (`WHERE record_kind = '...'`); a flat, non-STI
mapping would need every query to remember that filter by hand.

Attribute-renaming convention: each subclass exposes the OLD (pre-migration)
attribute names its callers already use, via plain `@property` (instance-
level only) or `@hybrid_property` (also usable in class-level `select()`/
`.where()`/`.order_by()` expressions) where the DDL's shared column name
differs. See each subclass docstring for its specific renames.

JSONB mutation rule (applies everywhere a JSONB dict/list is read-modified-
written, in these model properties and in every repository under
app/db/repositories/): always REASSIGN a new dict/list
(`self.domain_learning = {**self.domain_learning, "k": v}`), never mutate
in place (`self.domain_learning["k"] = v`). SQLAlchemy's JSONB change
tracking only fires on attribute reassignment; no `MutableDict`/
`MutableList` wrapper is configured.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WebIntelUnified(Base):
    __tablename__ = "webintel_unified"
    __mapper_args__ = {"polymorphic_on": "record_kind"}  # no polymorphic_identity -- never queried directly

    # -- identity / discriminator --
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_kind: Mapped[str] = mapped_column(Text, nullable=False)

    # -- logical IDs (stable across kinds; used instead of cross-table FKs) --
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    story_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    crawl_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    crawl_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    preflight_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    raw_artifact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # -- website / URL scope --
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_status: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- crawl job / run --
    crawl_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    crawl_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    crawl_priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    same_domain_only: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    crawl_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    crawl_statistics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    pages_discovered: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages_fetched: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages_extracted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_documents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_documents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unchanged_documents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    browser_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes_downloaded: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # -- per-page crawl attempt --
    crawl_page_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetch_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetch_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    crawl_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetch_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- raw artifact / object storage reference --
    storage_backend: Mapped[str] = mapped_column(Text, nullable=False, default="minio")
    storage_bucket: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    raw_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- dedup / hashing --
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    simhash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # -- extracted page fields (document grain) --
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    links: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    images: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    extracted_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # -- change detection (latest + history) --
    change_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_change_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_change_severity: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_change_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_change_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    latest_changed_fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    latest_change_diff: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    latest_change_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    versions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    changes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # -- NER / intelligence --
    entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    entity_language: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    entity_mentions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # -- story fields --
    story_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    story_canonical_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    story_document_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    story_source_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    story_entity_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    story_scoring_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    story_first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    story_last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    story_first_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    story_last_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    story_documents: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    story_entities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # On document rows: attachment to at most one primary story (API today).
    attached_story_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    story_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    story_match_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    story_match_confidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- domain / URL-pattern learning --
    preferred_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    circuit_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    crawl_delay_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    domain_learning: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # -- source monitoring --
    crawl_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    min_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consecutive_unchanged_crawls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_crawl_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_crawl_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    monitoring_events: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    source_urls: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # -- preflight snapshot --
    preflight_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    preflight_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    preflight_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- processing / ops --
    processing_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    intelligence_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    extractors_used: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    extractors_unavailable: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # -- timestamps --
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    # -- free-form extensibility --
    extras: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
