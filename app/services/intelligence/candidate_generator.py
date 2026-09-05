"""Candidate story generation (spec Phase 8 section 19): never compare a
new document against every story. Two cheap filters, both pushed into the
SQL query rather than done in Python -- entity overlap (join through
story_entities, indexed) and a page-type-aware time window (see
match_scorer.TEMPORAL_WINDOW_HOURS, reused here so blocking and scoring
agree on what "too old to matter" means).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.db.models.document import Document
from app.db.models.intelligence import Story
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.intelligence_repository import IntelligenceRepository
from app.services.intelligence.match_scorer import (
    DEFAULT_TEMPORAL_WINDOW_HOURS,
    TEMPORAL_WINDOW_HOURS,
    EntityRef,
    MatchCandidateInput,
)
from app.services.monitoring.fingerprints import from_signed_int64

BODY_SAMPLE_CHARS = 2_000  # bounded -- full-body RapidFuzz comparison is unnecessary cost for a lexical signal


async def find_candidates(
    intel_repo: IntelligenceRepository,
    doc_repo: DocumentRepository,
    *,
    document_id: uuid.UUID,
    document_title: str | None,
    document_body: str,
    document_simhash_signed: int | None,
    document_entity_ids: list[uuid.UUID],
    document_entity_types: dict[uuid.UUID, str],
    document_time: datetime | None,
    document_domain: str,
    document_page_type: str | None,
) -> list[tuple[Story, MatchCandidateInput]]:
    window_hours = TEMPORAL_WINDOW_HOURS.get(document_page_type or "", DEFAULT_TEMPORAL_WINDOW_HOURS)
    since = (document_time or datetime.now(tz=None)) - timedelta(hours=window_hours)

    stories = await intel_repo.find_candidate_stories(entity_ids=document_entity_ids, since=since)

    document_simhash = from_signed_int64(document_simhash_signed) if document_simhash_signed is not None else None
    document_entities = [
        EntityRef(entity_id=eid, entity_type=document_entity_types[eid]) for eid in document_entity_ids
    ]

    results: list[tuple[Story, MatchCandidateInput]] = []
    for story in stories:
        story_input = await _build_story_side(
            story, doc_repo, intel_repo,
            document_title=document_title, document_body=document_body[:BODY_SAMPLE_CHARS],
            document_simhash=document_simhash, document_entities=document_entities,
            document_time=document_time, document_domain=document_domain, document_page_type=document_page_type,
        )
        if story_input is not None:
            results.append((story, story_input))
    return results


async def _build_story_side(
    story: Story, doc_repo: DocumentRepository, intel_repo: IntelligenceRepository, *,
    document_title, document_body, document_simhash, document_entities, document_time, document_domain, document_page_type,
) -> MatchCandidateInput | None:
    representative: Document | None = None
    if story.representative_document_id is not None:
        representative = await doc_repo.get(story.representative_document_id)

    story_entities_rows = await intel_repo.get_story_entities(story.id)
    story_entity_ids = [row.entity_id for row in story_entities_rows]
    story_entities: list[EntityRef] = []
    for row in story_entities_rows:
        entity = await intel_repo.get_entity(row.entity_id)
        if entity is not None:
            story_entities.append(EntityRef(entity_id=entity.id, entity_type=entity.entity_type))

    sources = await intel_repo.get_story_sources(story.id)
    story_domains = [domain for domain, *_ in sources]

    story_simhash = None
    story_body_sample = ""
    if representative is not None:
        story_simhash = from_signed_int64(representative.simhash) if representative.simhash is not None else None
        story_body_sample = (representative.current_content or "")[:BODY_SAMPLE_CHARS]

    return MatchCandidateInput(
        document_title=document_title, document_body_sample=document_body, document_simhash=document_simhash,
        document_entities=document_entities, document_time=document_time, document_domain=document_domain,
        document_page_type=document_page_type,
        story_title=story.canonical_title, story_body_sample=story_body_sample, story_simhash=story_simhash,
        story_entities=story_entities, story_last_activity_at=story.last_activity_at, story_domains=story_domains,
    )
