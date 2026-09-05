from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CrawlRequest(BaseModel):
    url: str = Field(..., examples=["https://example-news-site.com"])
    max_pages: int | None = Field(default=None, ge=1, le=1000)
    max_depth: int | None = Field(default=None, ge=0, le=10)
    same_domain_only: bool = True


class CrawlJobResponse(BaseModel):
    crawl_id: UUID
    status: str
    seed_url: str
    max_pages: int
    max_depth: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    statistics: dict | None

    model_config = {"from_attributes": True}


class CrawlPageResponse(BaseModel):
    url: str
    normalized_url: str
    depth: int
    status: str
    fetch_strategy: str | None
    http_status: int | None
    document_id: UUID | None
    error: str | None

    model_config = {"from_attributes": True}
