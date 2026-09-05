from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CrawlPolicyInput(BaseModel):
    max_depth: int | None = Field(default=None, ge=0, le=10)
    max_pages: int | None = Field(default=None, ge=1, le=1000)
    same_domain_only: bool | None = None
    use_sitemap: bool | None = None
    use_rss: bool | None = None
    browser_mode: str | None = None


class SourceCreateRequest(BaseModel):
    name: str
    url: str
    preflight_id: UUID
    source_type: str = "unknown"
    interval_seconds: int = Field(default=900, ge=60, le=86400)
    max_interval_seconds: int = Field(default=86400, ge=60, le=604800)
    crawl_policy: CrawlPolicyInput | None = None


class SourceUpdateRequest(BaseModel):
    name: str | None = None
    source_type: str | None = None
    crawl_policy: CrawlPolicyInput | None = None


class SourceResponse(BaseModel):
    source_id: UUID
    name: str
    base_url: str
    domain: str
    source_type: str
    status: str
    crawl_policy: dict
    min_interval_seconds: int
    max_interval_seconds: int
    current_interval_seconds: int
    created_at: datetime
    updated_at: datetime
    last_crawl_at: datetime | None
    next_crawl_at: datetime | None


class MonitoringEventResponse(BaseModel):
    event_id: UUID
    document_id: UUID | None
    event_type: str
    previous_version: int | None
    new_version: int | None
    detected_at: datetime
    change_summary: dict | None


class SourceStatisticsResponse(BaseModel):
    source_id: UUID
    status: str
    current_interval_seconds: int
    consecutive_unchanged_crawls: int
    last_crawl_at: datetime | None
    next_crawl_at: datetime | None
    total_documents: int
    total_events: int
