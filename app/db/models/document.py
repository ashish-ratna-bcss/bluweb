"""`document` kind + its embedded-child shapes.

`Document` is a single-table-inheritance subclass of `WebIntelUnified`
(see app/db/models/unified.py for the shared column set and the JSONB
mutation rule). `DocumentVersion`/`DocumentChange` are no longer their own
table rows -- the unified schema embeds them as JSONB list entries on the
`documents.versions`/`documents.changes` columns -- so they're plain
dataclasses here, constructed by `DocumentRepository` from those entries and
returned to callers with the same shape as before. `RawArtifact` no longer
exists as a mapped class at all: its fields (`storage_key`, `content_type`,
`size`, `sha256`) are inlined directly into the `DocumentVersion` entry that
records them (see app/services/crawling/crawl_engine.py) instead of a
separate row referenced by `raw_artifact_id`.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.db.models.unified import WebIntelUnified


class Document(WebIntelUnified):
    __mapper_args__ = {"polymorphic_identity": "document"}

    @property
    def content_type(self) -> str | None:
        return self.page_content_type

    @content_type.setter
    def content_type(self, value: str | None) -> None:
        self.page_content_type = value

    @property
    def current_content(self) -> str | None:
        return self.content

    @current_content.setter
    def current_content(self, value: str | None) -> None:
        self.content = value


@dataclass
class DocumentVersion:
    id: uuid.UUID
    document_id: uuid.UUID
    version_number: int
    change_type: str  # NEW | UPDATED
    title: str | None
    content: str
    content_hash: str
    normalized_hash: str
    extraction_metadata: dict = field(default_factory=dict)
    created_at: datetime | None = None
    # Inlined former RawArtifact fields (no separate row/table anymore).
    raw_artifact_id: uuid.UUID | None = None
    storage_key: str | None = None
    raw_content_type: str | None = None
    raw_size_bytes: int | None = None
    raw_sha256: str | None = None


@dataclass
class DocumentChange:
    id: uuid.UUID
    document_id: uuid.UUID
    change_type: str
    severity: str
    previous_version_id: uuid.UUID | None = None
    current_version_id: uuid.UUID | None = None
    page_type: str | None = None
    similarity: float = 0.0
    change_confidence: float = 0.0
    changed_fields: list = field(default_factory=list)
    diff: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)
    created_at: datetime | None = None
