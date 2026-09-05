from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SearchFilters:
    query: str | None = None
    domain: str | None = None
    source_id: str | None = None
    source_type: str | None = None
    language: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = 20
    offset: int = 0


@dataclass
class SearchHit:
    document_id: str
    title: str | None
    url: str
    domain: str
    snippet: str
    published_at: datetime | None
    collected_at: datetime
    version: int
    content_hash: str
    relevance: float = 0.0


@dataclass
class SearchResults:
    hits: list[SearchHit] = field(default_factory=list)
    total: int = 0
