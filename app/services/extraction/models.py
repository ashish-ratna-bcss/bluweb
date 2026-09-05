from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ExtractedDocument:
    url: str
    title: str | None
    author: str | None
    published_at: datetime | None
    text: str
    language: str | None = None
    canonical_url: str | None = None
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    raw_metadata: dict = field(default_factory=dict)
