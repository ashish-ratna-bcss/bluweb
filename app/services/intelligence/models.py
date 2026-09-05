"""Phase 8 entity-intelligence domain models. Dataclasses -- same
convention as app/services/monitoring/models.py: internal service objects
stay plain dataclasses, Pydantic is reserved for the API layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class EntityType(StrEnum):
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    LOCATION = "LOCATION"
    EVENT = "EVENT"
    PRODUCT = "PRODUCT"
    VEHICLE = "VEHICLE"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    URL = "URL"
    MONEY = "MONEY"
    DATE = "DATE"
    TIME = "TIME"
    ADDRESS = "ADDRESS"
    USERNAME = "USERNAME"
    SOCIAL_HANDLE = "SOCIAL_HANDLE"


# Deterministic types never need a statistical model -- listed once here so
# every extractor/router agrees on which types short-circuit to regex.
DETERMINISTIC_TYPES = frozenset({
    EntityType.PHONE, EntityType.EMAIL, EntityType.URL,
    EntityType.MONEY, EntityType.DATE, EntityType.TIME,
})

# The GLiNER label set this system asks it to predict (spec section 11) --
# a controlled vocabulary, not "whatever GLiNER feels like returning."
GLINER_LABELS: dict[str, EntityType] = {
    "person": EntityType.PERSON,
    "organization": EntityType.ORGANIZATION,
    "location": EntityType.LOCATION,
    "event": EntityType.EVENT,
    "product": EntityType.PRODUCT,
    "vehicle": EntityType.VEHICLE,
    "online handle": EntityType.SOCIAL_HANDLE,
}


@dataclass
class EntityCandidate:
    """One raw extraction, before normalization/resolution."""

    raw_text: str
    entity_type: EntityType
    confidence: float
    extractor: str  # "regex" | "spacy" | "gliner" | "indicner"
    start_offset: int = -1
    end_offset: int | None = None
    context: str | None = None


@dataclass
class ExtractionResult:
    candidates: list[EntityCandidate] = field(default_factory=list)
    language_code: str | None = None
    extractors_used: list[str] = field(default_factory=list)
    extractors_unavailable: list[str] = field(default_factory=list)  # degraded-gracefully record
