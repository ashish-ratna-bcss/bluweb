import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True
    )

    crawl_type: Mapped[str] = mapped_column(String(16), nullable=False, default="instant")  # instant | monitoring
    seed_url: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    max_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    max_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    same_domain_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    statistics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    crawl_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    worker: Mapped[str] = mapped_column(String(64), nullable=False, default="inline")

    pages_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_attempted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_extracted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    new_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unchanged_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    http_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    browser_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_downloaded: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CrawlPage(Base):
    __tablename__ = "crawl_pages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    crawl_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|fetched|failed|skipped
    fetch_strategy: Mapped[str | None] = mapped_column(String(16), nullable=True)  # http|browser
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FetchStrategyStats(Base):
    """AKA the domain profile (spec Phase I): what we've learned about a
    domain's crawlability. Deliberately one table, not a parallel
    `DomainProfile` -- this table already *is* per-domain crawl history;
    adding failure-category and content-size tracking here is extending an
    existing seam, not duplicating one. `failure_counts` is a JSONB bag
    keyed by `FailureCategory` value rather than one column per category,
    so a new failure category never needs a migration.
    """

    __tablename__ = "fetch_strategy_stats"

    domain: Mapped[str] = mapped_column(String(255), primary_key=True)

    http_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    http_successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    http_extraction_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    browser_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    browser_successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    avg_http_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_browser_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_content_bytes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    failure_counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    success_status_counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # "2xx"/"3xx" -> count

    js_required_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    empty_content_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preferred_extractor: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Politeness / circuit breaker state (spec Phase 6 sections 24-27) --
    # derived from the counters above by DomainPolicyService, persisted here
    # so it survives process restarts rather than living only in memory.
    crawl_delay_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recommended_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    circuit_state: Mapped[str] = mapped_column(String(16), nullable=False, default="healthy")  # healthy|degraded|open
    circuit_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    # Phase 9 capability learning (spec sections 2/7): discovery status is a
    # DiscoveryStatus value (available/not_present/failed/blocked/invalid/
    # unknown), not a bare bool -- a block and a genuine absence mean
    # opposite things for routing and shouldn't collapse together.
    # avg_extraction_quality/quality_observations extend the existing
    # extraction_ok bool with score_extraction()'s continuous 0-1 score, so
    # "extraction succeeded but was mediocre" is learnable, not just
    # succeeded/failed.
    sitemap_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    feed_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    sitemap_url_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feed_url_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_extraction_quality: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_observations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Final completion: browser-vs-HTTP quality compare outcomes + completeness.
    browser_superior_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    http_superior_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    browser_equivalent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_completeness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    completeness_observations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    index_children_discovered_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class URLPatternStats(Base):
    """Per (domain, pattern, page_type) crawl history -- spec Phase 6
    section 7. `FetchStrategyStats` alone conflates `/news/*` with
    `/forum/*`; this is the finer-grained sibling it was missing, not a
    replacement for it. Same JSONB-bag-for-failures shape for consistency.
    """

    __tablename__ = "url_pattern_stats"
    __table_args__ = (
        UniqueConstraint("domain", "pattern", "page_type", name="uq_url_pattern_stats_domain_pattern_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    page_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    fetch_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_fetches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extraction_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extraction_successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    http_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    http_successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    browser_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    browser_successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    preferred_strategy: Mapped[str | None] = mapped_column(String(16), nullable=True)
    preferred_extractor: Mapped[str | None] = mapped_column(String(32), nullable=True)

    avg_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_content_bytes: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    failure_counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Phase 9 capability learning (spec section 3): pattern-level is the more
    # specific sibling of the domain-level fields added above.
    avg_extraction_quality: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_observations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pagination_detected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    browser_superior_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    http_superior_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    browser_equivalent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
