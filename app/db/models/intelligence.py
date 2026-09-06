"""`entity` / `story` kinds + their embedded-child shapes.

See app/db/models/unified.py for the shared column set, the STI rationale,
and the JSONB mutation rule. `EntityAlias`, `EntityMention`,
`StoryDocument`, `StoryEntity` are no longer their own table rows -- the
unified schema embeds them as JSONB list entries (`entities.entity_aliases`,
`documents.entity_mentions`, `stories.story_documents`,
`stories.story_entities`) -- so they're plain dataclasses here, constructed
by `IntelligenceRepository` from those entries.

`StoryDocument.domain`/`.published_at` are new fields with no old-schema
equivalent: `IntelligenceRepository.attach_document_to_story` enriches each
`story_documents` JSONB entry with the attached document's domain and
published_at at attach time, so aggregate recomputation
(`_recompute_story_aggregates`, `get_story_sources`) can iterate the one
JSONB array in Python instead of joining back to `documents` per entry.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.hybrid import hybrid_property

from app.db.models.unified import WebIntelUnified


class Entity(WebIntelUnified):
    __mapper_args__ = {"polymorphic_identity": "entity"}

    @property
    def confidence(self) -> float | None:
        return self.entity_confidence

    @confidence.setter
    def confidence(self, value: float | None) -> None:
        self.entity_confidence = value

    @property
    def language(self) -> str | None:
        return self.entity_language

    @language.setter
    def language(self, value: str | None) -> None:
        self.entity_language = value


@dataclass
class EntityAlias:
    id: uuid.UUID
    entity_id: uuid.UUID
    alias_text: str
    normalized_alias: str
    source: str  # "extraction" | "merge"
    language: str | None = None
    confidence: float = 0.5
    created_at: datetime | None = None


@dataclass
class EntityMention:
    id: uuid.UUID
    document_id: uuid.UUID
    entity_id: uuid.UUID
    raw_text: str
    normalized_text: str
    entity_type: str
    confidence: float
    extractor: str  # "regex" | "spacy" | "gliner" | "indicner"
    start_offset: int = -1  # -1 = offset not tracked
    end_offset: int | None = None
    context: str | None = None
    created_at: datetime | None = None


class Story(WebIntelUnified):
    __mapper_args__ = {"polymorphic_identity": "story"}

    @property
    def canonical_title(self) -> str | None:
        return self.story_canonical_title

    @canonical_title.setter
    def canonical_title(self, value: str | None) -> None:
        self.story_canonical_title = value

    @property
    def representative_document_id(self) -> uuid.UUID | None:
        return self.document_id

    @representative_document_id.setter
    def representative_document_id(self, value: uuid.UUID | None) -> None:
        self.document_id = value

    @property
    def first_seen_at(self) -> datetime | None:
        return self.story_first_seen_at

    @first_seen_at.setter
    def first_seen_at(self, value: datetime | None) -> None:
        self.story_first_seen_at = value

    @property
    def first_published_at(self) -> datetime | None:
        return self.story_first_published_at

    @first_published_at.setter
    def first_published_at(self, value: datetime | None) -> None:
        self.story_first_published_at = value

    @property
    def last_published_at(self) -> datetime | None:
        return self.story_last_published_at

    @last_published_at.setter
    def last_published_at(self, value: datetime | None) -> None:
        self.story_last_published_at = value

    @property
    def document_count(self) -> int | None:
        return self.story_document_count

    @document_count.setter
    def document_count(self, value: int | None) -> None:
        self.story_document_count = value

    @property
    def source_count(self) -> int | None:
        return self.story_source_count

    @source_count.setter
    def source_count(self, value: int | None) -> None:
        self.story_source_count = value

    @property
    def entity_count(self) -> int | None:
        return self.story_entity_count

    @entity_count.setter
    def entity_count(self, value: int | None) -> None:
        self.story_entity_count = value

    @property
    def scoring_version(self) -> str | None:
        return self.story_scoring_version

    @scoring_version.setter
    def scoring_version(self, value: str | None) -> None:
        self.story_scoring_version = value

    @hybrid_property
    def status(self) -> str | None:
        return self.story_status

    @status.setter
    def status(self, value: str | None) -> None:
        self.story_status = value

    @status.expression
    def status(cls):
        return cls.story_status

    @hybrid_property
    def last_activity_at(self) -> datetime | None:
        return self.story_last_activity_at

    @last_activity_at.setter
    def last_activity_at(self, value: datetime | None) -> None:
        self.story_last_activity_at = value

    @last_activity_at.expression
    def last_activity_at(cls):
        return cls.story_last_activity_at


@dataclass
class StoryDocument:
    id: uuid.UUID
    story_id: uuid.UUID
    document_id: uuid.UUID
    match_score: float
    match_method: str  # "seed" | "scored"
    confidence: str  # HIGH | MEDIUM | LOW
    feature_scores: dict = field(default_factory=dict)
    matching_evidence: list = field(default_factory=list)
    scoring_version: str = "v1"
    attached_at: datetime | None = None
    # Enrichment (no old-schema equivalent) -- see module docstring.
    domain: str | None = None
    published_at: datetime | None = None


@dataclass
class StoryEntity:
    id: uuid.UUID
    story_id: uuid.UUID
    entity_id: uuid.UUID
    mention_count: int = 1
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    importance: float = 0.5
