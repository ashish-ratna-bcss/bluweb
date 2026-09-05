import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


DEFAULT_CRAWL_POLICY = {
    "max_depth": 3,
    "max_pages": 100,
    "same_domain_only": True,
    "use_sitemap": True,
    "use_rss": True,
    "browser_mode": "auto",
}


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="paused", index=True)  # paused|active

    preflight_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("preflight_reports.id", ondelete="SET NULL"), nullable=True
    )
    crawl_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=lambda: dict(DEFAULT_CRAWL_POLICY))

    min_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    max_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=86400)
    current_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    consecutive_unchanged_crawls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    last_crawl_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_crawl_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class SourceUrl(Base):
    __tablename__ = "source_urls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active|removed
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_failure_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MonitoringEvent(Base):
    __tablename__ = "monitoring_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    event_type: Mapped[str] = mapped_column(String(16), nullable=False)  # NEW|UPDATED|UNCHANGED|REMOVED|RESTORED|CRAWL_FAILED
    previous_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, index=True)
