from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str | None = None
    domain: str | None = None
    source_id: str | None = None
    language: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class InstantSearchRequest(BaseModel):
    url: str
    query: str | None = None
    max_pages: int = Field(default=5, ge=1, le=50)
    wait_seconds: float = Field(default=8.0, ge=0, le=30)


class SearchHitResponse(BaseModel):
    document_id: str
    title: str | None
    url: str
    domain: str
    snippet: str
    published_at: datetime | None
    collected_at: datetime
    version: int
    content_hash: str
    relevance: float


class SearchResponse(BaseModel):
    total: int
    results: list[SearchHitResponse]


class InstantSearchResponse(BaseModel):
    crawl_id: str
    crawl_status: str
    total: int
    results: list[SearchHitResponse]
    note: str
