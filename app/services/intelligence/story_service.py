"""Phase 8 orchestrator (spec sections 25/40): the single entry point
crawl_engine.py calls after a document is created/updated. Runs entity
extraction -> resolution -> mention recording -> candidate generation ->
scoring -> attach-or-create, and returns an explainable `IntelligenceOutcome`
matching the JSON shape spec section 40 asks for.

Deliberately NOT run on `UNCHANGED` documents (crawl_engine.py only calls
this on NEW/UPDATED) -- re-running NER/resolution/scoring against content
that provably didn't change (Phase 7's own ChangeDetectionService already
answered that question) would be exactly the "unnecessary expensive
reprocessing" spec section 37 asks to avoid.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.db.models.intelligence import Entity, Story
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.intelligence_repository import IntelligenceRepository
from app.services.intelligence.candidate_generator import find_candidates
from app.services.intelligence.entity_extractor import extract_entities
from app.services.intelligence.entity_normalizer import normalize_for_alias_matching, normalize_name
from app.services.intelligence.entity_resolver import ExistingEntity, resolve_entity
from app.services.intelligence.match_scorer import MatchDecision, score_candidate
from app.services.intelligence.temporal_extractor import extract_temporal_mentions, resolve_document_time

MAX_ENTITY_CANDIDATES_PER_TYPE = 100  # bounded DB lookup per resolution -- see entity_resolver.py's fuzzy-match cost note


@dataclass
class ResolvedEntityInfo:
    entity_id: uuid.UUID
    entity_type: str
    canonical_name: str


@dataclass
class IntelligenceOutcome:
    entities: list[ResolvedEntityInfo] = field(default_factory=list)
    story_id: uuid.UUID | None = None
    story_decision: str = "NONE"  # "ATTACH" | "NEW_STORY" | "NONE" (no entities/candidates at all)
    candidate_count: int = 0
    resolution_counts: dict[str, int] = field(default_factory=dict)
    match_score: float | None = None
    confidence: str | None = None
    reasons: list[str] = field(default_factory=list)
    feature_scores: dict[str, float] = field(default_factory=dict)
    language_code: str | None = None
    extractors_used: list[str] = field(default_factory=list)
    extractors_unavailable: list[str] = field(default_factory=list)


async def process_document_intelligence(
    session,
    *,
    document_id: uuid.UUID,
    title: str | None,
    body: str,
    simhash_signed: int | None,
    page_type: str | None,
    domain: str,
    published_at: datetime | None,
    updated_at: datetime | None,
) -> IntelligenceOutcome:
    intel_repo = IntelligenceRepository(session)
    doc_repo = DocumentRepository(session)

    extraction = extract_entities(f"{title or ''}\n\n{body}")
    document_time = resolve_document_time(
        published_at=published_at, updated_at=updated_at,
        text_mentions=extract_temporal_mentions(body, reference_date=updated_at or published_at),
    )

    resolved, resolution_counts = await _resolve_and_record_entities(
        intel_repo, document_id=document_id, candidates=extraction.candidates,
        language=extraction.language_code, seen_at=document_time or datetime.now(timezone.utc),
    )

    outcome = IntelligenceOutcome(
        entities=[ResolvedEntityInfo(r.id, r.entity_type, r.canonical_name) for r in resolved],
        language_code=extraction.language_code,
        extractors_used=extraction.extractors_used,
        extractors_unavailable=extraction.extractors_unavailable,
        resolution_counts=resolution_counts,
    )

    if not resolved:
        return outcome  # no entities at all -- nothing to correlate; story_decision stays "NONE"

    entity_types = {r.id: r.entity_type for r in resolved}
    candidates = await find_candidates(
        intel_repo, doc_repo,
        document_id=document_id, document_title=title, document_body=body,
        document_simhash_signed=simhash_signed, document_entity_ids=[r.id for r in resolved],
        document_entity_types=entity_types, document_time=document_time, document_domain=domain,
        document_page_type=page_type,
    )
    outcome.candidate_count = len(candidates)

    best_story: Story | None = None
    best_result = None
    for story, match_input in candidates:
        result = score_candidate(match_input)
        if result.decision == MatchDecision.ATTACH and (best_result is None or result.score > best_result.score):
            best_story, best_result = story, result

    if best_story is not None and best_result is not None:
        await intel_repo.attach_document_to_story(
            story=best_story, document_id=document_id, match_score=best_result.score, match_method="scored",
            confidence=best_result.confidence.value, feature_scores=best_result.feature_scores,
            matching_evidence=best_result.reasons, scoring_version=best_result.scoring_version,
            document_domain=domain, document_published_at=document_time, document_entity_ids=[r.id for r in resolved],
        )
        outcome.story_id = best_story.id
        outcome.story_decision = "ATTACH"
        outcome.match_score = best_result.score
        outcome.confidence = best_result.confidence.value
        outcome.reasons = best_result.reasons
        outcome.feature_scores = best_result.feature_scores
        return outcome

    # No candidate cleared the bar -- create a new story with this document as its seed/representative.
    now = document_time or datetime.now(timezone.utc)
    story = await intel_repo.create_story(
        canonical_title=title, representative_document_id=document_id,
        status="ACTIVE", first_seen_at=now, last_activity_at=now,
        first_published_at=document_time, last_published_at=document_time,
        document_count=0, source_count=0, entity_count=0,
    )
    await intel_repo.attach_document_to_story(
        story=story, document_id=document_id, match_score=1.0, match_method="seed",
        confidence="HIGH", feature_scores={}, matching_evidence=["first document in a new story"],
        scoring_version="v1", document_domain=domain, document_published_at=document_time,
        document_entity_ids=[r.id for r in resolved],
    )
    outcome.story_id = story.id
    outcome.story_decision = "NEW_STORY"
    outcome.confidence = "HIGH"
    outcome.reasons = ["no existing story matched -- created new story"]
    return outcome


async def _resolve_and_record_entities(
    intel_repo: IntelligenceRepository, *, document_id: uuid.UUID, candidates, language: str | None, seen_at: datetime,
) -> tuple[list[Entity], dict[str, int]]:
    document_context_names = [c.raw_text for c in candidates]
    resolved: list[Entity] = []
    resolution_counts: dict[str, int] = {}
    type_cache: dict[str, list[ExistingEntity]] = {}

    for candidate in candidates:
        entity_type_value = candidate.entity_type.value
        if entity_type_value not in type_cache:
            db_entities = await intel_repo.find_entities_by_type(entity_type_value, limit=MAX_ENTITY_CANDIDATES_PER_TYPE)
            alias_map = await intel_repo.get_aliases_for_entities([e.id for e in db_entities])
            existing_wrapped = []
            for e in db_entities:
                context_names = await intel_repo.get_entity_context_names(e.id)
                existing_wrapped.append(ExistingEntity(
                    entity_id=e.id, entity_type=candidate.entity_type, canonical_name=e.canonical_name,
                    normalized_name=e.normalized_name, aliases=alias_map.get(e.id, []),
                    known_context_names=context_names,
                ))
            type_cache[entity_type_value] = existing_wrapped

        decision = resolve_entity(
            candidate, candidates_same_type=type_cache[entity_type_value],
            document_context_names=[n for n in document_context_names if n != candidate.raw_text],
        )
        resolution_counts[decision.action] = resolution_counts.get(decision.action, 0) + 1

        if decision.action == "NEW_ENTITY":
            normalized = normalize_name(candidate.raw_text)
            entity, created = await intel_repo.get_or_create_entity_locked(
                entity_type=entity_type_value, canonical_name=candidate.raw_text.strip(),
                normalized_name=normalized, language=language, confidence=candidate.confidence,
            )
            if created:
                type_cache[entity_type_value].append(ExistingEntity(
                    entity_id=entity.id, entity_type=candidate.entity_type, canonical_name=entity.canonical_name,
                    normalized_name=entity.normalized_name, aliases=[], known_context_names=[],
                ))
        else:
            entity = await intel_repo.get_entity(decision.entity_id)
            if entity is None:
                continue
            if decision.action == "NEW_ALIAS":
                await intel_repo.add_alias_if_missing(
                    entity_id=entity.id, alias_text=candidate.raw_text.strip(),
                    normalized_alias=normalize_for_alias_matching(candidate.raw_text),
                    language=language, source="extraction", confidence=candidate.confidence,
                )

        await intel_repo.touch_entity_last_seen(entity, seen_at=seen_at)
        await intel_repo.add_mention(
            document_id=document_id, entity_id=entity.id, raw_text=candidate.raw_text,
            normalized_text=normalize_name(candidate.raw_text), entity_type=entity_type_value,
            confidence=candidate.confidence, extractor=candidate.extractor,
            start_offset=candidate.start_offset, end_offset=candidate.end_offset,
        )
        resolved.append(entity)

    return resolved, resolution_counts
