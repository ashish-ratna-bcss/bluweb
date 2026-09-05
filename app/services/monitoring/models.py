"""Phase 7 change-detection domain models. Dataclasses, not Pydantic --
matches the existing convention for internal service objects (HealthScore/
RoutingDecision/PolicyState in app/services/crawling/*_service.py); Pydantic
stays reserved for the API request/response layer (app/schemas/*.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ChangeType(StrEnum):
    NO_CHANGE = "NO_CHANGE"
    CONTENT_CHANGED = "CONTENT_CHANGED"
    TITLE_CHANGED = "TITLE_CHANGED"
    METADATA_CHANGED = "METADATA_CHANGED"
    ARTICLE_UPDATED = "ARTICLE_UPDATED"  # title + body both changed together
    PRICE_CHANGED = "PRICE_CHANGED"
    STATUS_CHANGED = "STATUS_CHANGED"
    DESCRIPTION_CHANGED = "DESCRIPTION_CHANGED"
    NEW_POSTS = "NEW_POSTS"
    POST_EDITED = "POST_EDITED"
    POST_REMOVED = "POST_REMOVED"
    LISTING_ADDED = "LISTING_ADDED"
    LISTING_REMOVED = "LISTING_REMOVED"
    STRUCTURE_CHANGED = "STRUCTURE_CHANGED"


class Severity(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class DocumentSnapshot:
    """The subset of Document/extraction fields change detection needs.
    Built by the caller (crawl_engine.py) from either the existing ORM row
    (previous) or the freshly extracted document (current) -- kept as a
    plain dataclass so the service itself stays DB-independent and unit
    testable, same pattern as domain_profile_service.py."""

    title: str | None
    author: str | None
    published_at: datetime | None
    body: str
    images: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    simhash: int | None = None


@dataclass
class ChangeResult:
    changed: bool
    change_type: ChangeType
    severity: Severity
    similarity: float  # 0..1 fingerprint-derived closeness (1.0 = identical)
    change_confidence: float  # 0..1 deterministic confidence in this classification -- NOT an ML probability
    changed_fields: list[str] = field(default_factory=list)
    added_fields: list[str] = field(default_factory=list)
    removed_fields: list[str] = field(default_factory=list)
    modified_fields: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    diff: dict[str, Any] = field(default_factory=dict)  # compact, JSON-serializable field-level summary
