"""Entity resolution (spec Phase 8 sections 12/33): decide whether a
freshly extracted entity candidate is the same real-world entity as one
already in the `entities` table, a new alias of one, or a genuinely new
entity. Pure decision logic -- DB lookups (exact-name, alias, and
pg_trgm/RapidFuzz candidate fetch) happen in the repository layer and are
handed to this module as plain `ExistingEntity` rows, matching the
domain_profile_service.py convention of keeping decision logic testable
without a database.

Pipeline (section 12): exact normalization -> alias lookup -> RapidFuzz
candidate scoring -> context/type validation -> merge decision.

PERSON safety rule (section 33's own worked example: two different real
"Ravi Kumar"s must not silently become one entity): unlike ORGANIZATION/
LOCATION, an exact-name match on a PERSON is NOT enough on its own to
merge. It also requires *corroboration* -- at least one other entity
(an ORGANIZATION or LOCATION) co-occurring in the current document that
also co-occurred with the existing entity in some prior document. Two
unrelated "Ravi Kumar" mentions in unrelated contexts (different
co-occurring orgs/locations, or none at all) each get their own Entity
row; the same "Ravi Kumar" recurring in the same real-world context
(the same organization/location showing up again) correctly merges --
this is exactly the cross-source-correlation case the spec's own news
example depends on (the same public figure named across BBC/local/blog
coverage of the same story).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.intelligence.entity_normalizer import normalize_for_alias_matching, normalize_name
from app.services.intelligence.models import EntityCandidate, EntityType

try:
    from rapidfuzz import fuzz
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:  # noqa: BLE001 - optional dependency, degrade per spec section 46
    _RAPIDFUZZ_AVAILABLE = False

# Suffix-normalized fuzzy match must be this close to auto-attach as an
# alias ("Microsoft Corporation" vs "Microsoft Corp." scored 80 on raw
# token_sort_ratio in live testing -- suffix stripping first pushes true
# variants well above this bar while keeping unrelated names well below it).
FUZZY_ALIAS_THRESHOLD = 85.0

PERSON_TYPES = frozenset({EntityType.PERSON})
# Types that never require corroboration -- name collision risk is low
# (organizations/locations are named far more distinctively than people)
# and these are exactly the types the spec's cross-source examples need
# linked without extra friction.
NO_CORROBORATION_REQUIRED = frozenset({
    EntityType.ORGANIZATION, EntityType.LOCATION, EntityType.PHONE,
    EntityType.EMAIL, EntityType.URL, EntityType.USERNAME, EntityType.SOCIAL_HANDLE,
})


@dataclass
class ExistingEntity:
    entity_id: object  # uuid.UUID, kept generic so this module has no DB import
    entity_type: EntityType
    canonical_name: str
    normalized_name: str
    aliases: list[str] = field(default_factory=list)  # normalized alias strings already on file
    known_context_names: list[str] = field(default_factory=list)  # normalized names of entities previously co-occurring with this one


@dataclass
class ResolutionDecision:
    action: str  # "MERGE" | "NEW_ALIAS" | "NEW_ENTITY"
    entity_id: object | None
    confidence: float
    reasons: list[str] = field(default_factory=list)


def resolve_entity(
    candidate: EntityCandidate,
    *,
    candidates_same_type: list[ExistingEntity],
    document_context_names: list[str],
) -> ResolutionDecision:
    """`candidates_same_type` should already be filtered to the same
    `entity_type` by the caller's DB query -- this function never merges
    across types (a LOCATION and an ORGANIZATION with the same string are
    never the same entity)."""
    normalized = normalize_name(candidate.raw_text)
    alias_normalized = normalize_for_alias_matching(candidate.raw_text)
    context_set = {normalize_name(n) for n in document_context_names}

    for existing in candidates_same_type:
        if existing.normalized_name == normalized:
            return _decide_name_match(existing, context_set, match_kind="exact name")
        if normalized in existing.aliases:
            return _decide_name_match(existing, context_set, match_kind="known alias")

    if _RAPIDFUZZ_AVAILABLE:
        best_existing, best_score = _best_fuzzy_match(alias_normalized, candidates_same_type)
        if best_existing is not None and best_score >= FUZZY_ALIAS_THRESHOLD:
            decision = _decide_name_match(best_existing, context_set, match_kind=f"fuzzy match ({best_score:.0f})")
            if decision.action == "MERGE":
                decision.action = "NEW_ALIAS"  # fuzzy matches attach as an alias, never a silent full merge
            return decision

    return ResolutionDecision(action="NEW_ENTITY", entity_id=None, confidence=0.6, reasons=["no matching entity found"])


def _decide_name_match(existing: ExistingEntity, context_set: set[str], *, match_kind: str) -> ResolutionDecision:
    if existing.entity_type not in PERSON_TYPES or existing.entity_type in NO_CORROBORATION_REQUIRED:
        return ResolutionDecision(
            action="MERGE", entity_id=existing.entity_id, confidence=0.9, reasons=[f"{match_kind}, no corroboration required for this type"],
        )

    known_context = {normalize_name(n) for n in existing.known_context_names}
    shared = context_set & known_context
    if shared:
        return ResolutionDecision(
            action="MERGE", entity_id=existing.entity_id, confidence=0.85,
            reasons=[f"{match_kind}, corroborated by shared context: {sorted(shared)}"],
        )

    return ResolutionDecision(
        action="NEW_ENTITY", entity_id=None, confidence=0.5,
        reasons=[f"{match_kind} but no corroborating context -- treated as a distinct person (spec section 33 safety rule)"],
    )


def _best_fuzzy_match(
    alias_normalized: str, candidates: list[ExistingEntity],
) -> tuple[ExistingEntity | None, float]:
    best_existing: ExistingEntity | None = None
    best_score = 0.0
    for existing in candidates:
        existing_alias_form = normalize_for_alias_matching(existing.canonical_name)
        score = fuzz.token_sort_ratio(alias_normalized, existing_alias_form)
        if score > best_score:
            best_score = score
            best_existing = existing
    return best_existing, best_score
