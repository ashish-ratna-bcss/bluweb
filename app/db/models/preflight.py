import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _default_expiration() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=24)


class PreflightReportRow(Base):
    __tablename__ = "preflight_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    capability_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="low")

    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    crawler_version: Mapped[str] = mapped_column(String(32), nullable=False, default="0.1.0")
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False, default="trafilatura-2.2.0")

    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_default_expiration
    )
