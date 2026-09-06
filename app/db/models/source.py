"""`source` kind + its embedded-child shapes.

See app/db/models/unified.py for the shared column set, the STI rationale,
and the JSONB mutation rule. `SourceUrl`/`MonitoringEvent` are no longer
their own table rows -- the unified schema embeds them as JSONB list
entries (`sources.source_urls`, `sources.monitoring_events`) -- so they're
plain dataclasses here, constructed by `SourceRepository` from those
entries.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.hybrid import hybrid_property

from app.db.models.unified import WebIntelUnified

DEFAULT_CRAWL_POLICY = {
    "max_depth": 3,
    "max_pages": 100,
    "same_domain_only": True,
    "use_sitemap": True,
    "use_rss": True,
    "browser_mode": "auto",
}


class Source(WebIntelUnified):
    __mapper_args__ = {"polymorphic_identity": "source"}

    @property
    def name(self) -> str | None:
        return self.source_name

    @name.setter
    def name(self, value: str | None) -> None:
        self.source_name = value

    @property
    def base_url(self) -> str | None:
        return self.url

    @base_url.setter
    def base_url(self, value: str | None) -> None:
        self.url = value

    @hybrid_property
    def status(self) -> str | None:
        return self.source_status

    @status.setter
    def status(self, value: str | None) -> None:
        self.source_status = value

    @status.expression
    def status(cls):
        return cls.source_status


@dataclass
class SourceUrl:
    id: uuid.UUID
    source_id: uuid.UUID
    url: str
    normalized_url: str
    status: str = "active"  # active|removed
    consecutive_failures: int = 0
    last_failure_category: str | None = None
    document_id: uuid.UUID | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    last_crawled_at: datetime | None = None


@dataclass
class MonitoringEvent:
    id: uuid.UUID
    source_id: uuid.UUID
    event_type: str  # NEW|UPDATED|UNCHANGED|REMOVED|RESTORED|CRAWL_FAILED
    document_id: uuid.UUID | None = None
    previous_version: int | None = None
    new_version: int | None = None
    change_summary: dict | None = None
    detected_at: datetime | None = None
