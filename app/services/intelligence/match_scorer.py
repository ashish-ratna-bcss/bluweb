"""Deterministic story-match scoring (spec Phase 8 section 21-22). Pure
function -- no DB, no model calls -- same convention as
app/services/monitoring/change_detection.py.

Weights are a fixed, documented, versioned ("v1") constant, not learned or
tuned against a labeled set (none exists yet -- section 21 explicitly asks
for documented, tested initial weights, not machine-learned ones).
entity_overlap gets the largest weight because it's the most specific
signal available (two documents naming the same organization and location
is far stronger evidence than similar wording); temporal_proximity is
second because the spec's own worked example (section 17: articles A/B/C
minutes apart form one story, article D a week later does not) hinges on
it being able to veto an otherwise-plausible match on its own.

Anti-generic-overlap safeguard (section Z: "police" and "arrest" appearing
in unrelated articles must not correlate them): `entity_overlap` weights a
shared PERSON-only match far below a shared ORGANIZATION/LOCATION match,
since common names and common role-nouns are exactly the collision risk
named in the spec -- without a corpus-wide entity-frequency table (out of
scope for Phase 8), down-weighting by type is the deterministic
approximation of IDF this system can afford right now.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.services.intelligence.models import EntityType
from app.services.monitoring.fingerprints import simhash_similarity

try:
    from rapidfuzz import fuzz
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:  # noqa: BLE001
    _RAPIDFUZZ_AVAILABLE = False

SCORING_VERSION = "v1"

# Sums to 1.0 -- documented, not tuned against labeled data (none exists yet).
FEATURE_WEIGHTS: dict[str, float] = {
    "entity_overlap": 0.30,
    "title_similarity": 0.20,
    "temporal_proximity": 0.15,
    "body_similarity": 0.10,
    "simhash_similarity": 0.10,
    "location_overlap": 0.10,
    "source_similarity": 0.05,
}
assert abs(sum(FEATURE_WEIGHTS.values()) - 1.0) < 1e-9

HIGH_CONFIDENCE_THRESHOLD = 0.65
MEDIUM_CONFIDENCE_THRESHOLD = 0.45
# Medium-confidence safeguard (section 22): a mediocre overall score only
# attaches if entity overlap is real, not just lexical noise.
MEDIUM_SAFEGUARD_MIN_ENTITY_OVERLAP = 0.30

# Time windows beyond which temporal_proximity is treated as zero --
# forum discussion of a days-old story is normal; a classified listing
# "matching" a months-old one almost certainly isn't the same listing.
TEMPORAL_WINDOW_HOURS = {
    "NEWS_ARTICLE": 72.0, "BLOG_POST": 72.0,
    "FORUM_THREAD": 168.0, "FORUM_INDEX": 168.0, "DISCUSSION_THREAD": 168.0,
    "CLASSIFIED_LISTING": 720.0, "CLASSIFIED_INDEX": 720.0,
}
DEFAULT_TEMPORAL_WINDOW_HOURS = 72.0

_PERSON_ENTITY_WEIGHT = 0.3  # down-weight per the anti-generic-overlap safeguard above


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class MatchDecision(StrEnum):
    ATTACH = "ATTACH"
    REJECT = "REJECT"


@dataclass
class EntityRef:
    entity_id: object
    entity_type: EntityType


@dataclass
class MatchCandidateInput:
    """Everything the scorer needs about one (document, candidate story)
    pair. Built by candidate_generator.py from DB rows -- kept as a plain
    dataclass so this module stays DB-independent."""

    document_title: str | None
    document_body_sample: str  # bounded sample, not the full body -- see candidate_generator.py
    document_simhash: int | None
    document_entities: list[EntityRef]
    document_time: datetime | None
    document_domain: str | None
    document_page_type: str | None

    story_title: str | None
    story_body_sample: str
    story_simhash: int | None
    story_entities: list[EntityRef]
    story_last_activity_at: datetime | None
    story_domains: list[str] = field(default_factory=list)


@dataclass
class MatchResult:
    decision: MatchDecision
    confidence: Confidence
    score: float
    feature_scores: dict[str, float]
    reasons: list[str]
    scoring_version: str = SCORING_VERSION


def score_candidate(inp: MatchCandidateInput) -> MatchResult:
    features = {
        "entity_overlap": _entity_overlap(inp.document_entities, inp.story_entities),
        "title_similarity": _text_similarity(inp.document_title, inp.story_title),
        "body_similarity": _text_similarity(inp.document_body_sample, inp.story_body_sample),
        "simhash_similarity": simhash_similarity(inp.document_simhash, inp.story_simhash),
        "location_overlap": _location_overlap(inp.document_entities, inp.story_entities),
        "temporal_proximity": _temporal_proximity(
            inp.document_time, inp.story_last_activity_at, inp.document_page_type,
        ),
        "source_similarity": _source_similarity(inp.document_domain, inp.story_domains),
    }

    total = sum(FEATURE_WEIGHTS[name] * value for name, value in features.items())
    reasons = _build_reasons(features)
    has_specific_overlap = _has_specific_entity_overlap(inp.document_entities, inp.story_entities)

    if total >= HIGH_CONFIDENCE_THRESHOLD:
        return MatchResult(MatchDecision.ATTACH, Confidence.HIGH, total, features, reasons)

    if total >= MEDIUM_CONFIDENCE_THRESHOLD:
        # Section Z's safeguard is about *specific* overlap, not the
        # penalized numeric score alone -- a person-only match's score is
        # capped low but could still coincidentally clear a bare numeric
        # bar; requiring at least one shared non-PERSON entity is the
        # actual guarantee the spec asks for.
        if features["entity_overlap"] >= MEDIUM_SAFEGUARD_MIN_ENTITY_OVERLAP and has_specific_overlap:
            reasons.append("medium-confidence safeguard passed: real entity overlap present")
            return MatchResult(MatchDecision.ATTACH, Confidence.MEDIUM, total, features, reasons)
        reasons.append("medium-confidence safeguard failed: insufficient specific entity overlap to attach")
        return MatchResult(MatchDecision.REJECT, Confidence.MEDIUM, total, features, reasons)

    return MatchResult(MatchDecision.REJECT, Confidence.LOW, total, features, reasons)


def _entity_overlap(doc_entities: list[EntityRef], story_entities: list[EntityRef]) -> float:
    doc_ids = {e.entity_id: e.entity_type for e in doc_entities}
    story_ids = {e.entity_id: e.entity_type for e in story_entities}
    if not doc_ids or not story_ids:
        return 0.0

    shared = set(doc_ids) & set(story_ids)
    if not shared:
        return 0.0

    union = set(doc_ids) | set(story_ids)
    base_ratio = len(shared) / len(union)

    # Plain Jaccard alone can't distinguish "shared a specific org+location"
    # from "shared only a common person name" when that's the only entity
    # present on both sides (the ratio comes out 1.0 either way) -- an
    # explicit penalty multiplier when EVERY shared entity is a PERSON is
    # the deterministic fix, applied only in that all-person case so a
    # real org/location match is never discounted by an incidental shared
    # person name riding along with it.
    if all(doc_ids[eid] == EntityType.PERSON for eid in shared):
        return base_ratio * _PERSON_ENTITY_WEIGHT
    return base_ratio


def _has_specific_entity_overlap(doc_entities: list[EntityRef], story_entities: list[EntityRef]) -> bool:
    doc_ids = {e.entity_id: e.entity_type for e in doc_entities}
    story_ids = {e.entity_id: e.entity_type for e in story_entities}
    shared = set(doc_ids) & set(story_ids)
    return any(doc_ids[eid] != EntityType.PERSON for eid in shared)


def _location_overlap(doc_entities: list[EntityRef], story_entities: list[EntityRef]) -> float:
    doc_locs = {e.entity_id for e in doc_entities if e.entity_type == EntityType.LOCATION}
    story_locs = {e.entity_id for e in story_entities if e.entity_type == EntityType.LOCATION}
    if not doc_locs or not story_locs:
        return 0.0
    return 1.0 if doc_locs & story_locs else 0.0


def _text_similarity(a: str | None, b: str | None) -> float:
    if not a or not b or not _RAPIDFUZZ_AVAILABLE:
        return 0.0
    return fuzz.token_sort_ratio(a, b) / 100.0


def _temporal_proximity(
    doc_time: datetime | None, story_time: datetime | None, page_type: str | None,
) -> float:
    if doc_time is None or story_time is None:
        return 0.0
    window_hours = TEMPORAL_WINDOW_HOURS.get(page_type or "", DEFAULT_TEMPORAL_WINDOW_HOURS)
    delta_hours = abs((doc_time - story_time).total_seconds()) / 3600.0
    if delta_hours >= window_hours:
        return 0.0
    return max(0.0, 1.0 - (delta_hours / window_hours))


def _source_similarity(doc_domain: str | None, story_domains: list[str]) -> float:
    if not doc_domain:
        return 0.0
    if doc_domain in story_domains:
        return 0.5  # same-source repost of an already-known story: real, but not the cross-source case this phase is built for
    return 1.0 if story_domains else 0.0  # a genuinely new source is the strongest positive signal for THIS feature specifically


def _build_reasons(features: dict[str, float]) -> list[str]:
    reasons = []
    if features["entity_overlap"] > 0:
        reasons.append(f"entity overlap {features['entity_overlap']:.2f}")
    if features["title_similarity"] > 0.5:
        reasons.append(f"title similarity {features['title_similarity']:.2f}")
    if features["location_overlap"] > 0:
        reasons.append("shared location")
    if features["temporal_proximity"] > 0.5:
        reasons.append(f"temporal proximity {features['temporal_proximity']:.2f}")
    if features["simhash_similarity"] > 0.8:
        reasons.append(f"high content similarity (simhash {features['simhash_similarity']:.2f})")
    return reasons
