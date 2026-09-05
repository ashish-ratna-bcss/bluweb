import uuid

from app.services.intelligence.entity_resolver import ExistingEntity, resolve_entity
from app.services.intelligence.models import EntityCandidate, EntityType


def _existing(entity_type, canonical_name, *, aliases=None, known_context_names=None) -> ExistingEntity:
    from app.services.intelligence.entity_normalizer import normalize_name
    return ExistingEntity(
        entity_id=uuid.uuid4(), entity_type=entity_type, canonical_name=canonical_name,
        normalized_name=normalize_name(canonical_name),
        aliases=[normalize_name(a) for a in (aliases or [])],
        known_context_names=known_context_names or [],
    )


def test_exact_name_match_merges_organization():
    existing = _existing(EntityType.ORGANIZATION, "Hyderabad Police")
    candidate = EntityCandidate(raw_text="HYDERABAD POLICE", entity_type=EntityType.ORGANIZATION, confidence=0.8, extractor="spacy")
    decision = resolve_entity(candidate, candidates_same_type=[existing], document_context_names=[])
    assert decision.action == "MERGE"
    assert decision.entity_id == existing.entity_id


def test_corporate_suffix_variant_attaches_as_alias():
    existing = _existing(EntityType.ORGANIZATION, "Microsoft Corporation")
    candidate = EntityCandidate(raw_text="Microsoft Corp.", entity_type=EntityType.ORGANIZATION, confidence=0.8, extractor="spacy")
    decision = resolve_entity(candidate, candidates_same_type=[existing], document_context_names=[])
    assert decision.action == "NEW_ALIAS"
    assert decision.entity_id == existing.entity_id


def test_unrelated_names_do_not_merge():
    existing = _existing(EntityType.ORGANIZATION, "Hyderabad Police")
    candidate = EntityCandidate(raw_text="Delhi Police", entity_type=EntityType.ORGANIZATION, confidence=0.8, extractor="spacy")
    decision = resolve_entity(candidate, candidates_same_type=[existing], document_context_names=[])
    assert decision.action == "NEW_ENTITY"


def test_person_same_name_without_corroboration_does_not_merge():
    existing = _existing(EntityType.PERSON, "Ravi Kumar", known_context_names=["Bangalore Tech Summit"])
    candidate = EntityCandidate(raw_text="Ravi Kumar", entity_type=EntityType.PERSON, confidence=0.8, extractor="spacy")
    decision = resolve_entity(candidate, candidates_same_type=[existing], document_context_names=["Delhi Crime Branch"])
    assert decision.action == "NEW_ENTITY"
    assert "safety rule" in decision.reasons[0]


def test_person_same_name_with_corroborating_context_merges():
    existing = _existing(EntityType.PERSON, "Narendra Modi", known_context_names=["Hyderabad", "Microsoft Corporation"])
    candidate = EntityCandidate(raw_text="Narendra Modi", entity_type=EntityType.PERSON, confidence=0.9, extractor="spacy")
    decision = resolve_entity(candidate, candidates_same_type=[existing], document_context_names=["Hyderabad", "ABC Corp"])
    assert decision.action == "MERGE"
    assert decision.entity_id == existing.entity_id


def test_no_candidates_creates_new_entity():
    candidate = EntityCandidate(raw_text="Some Brand New Org", entity_type=EntityType.ORGANIZATION, confidence=0.8, extractor="spacy")
    decision = resolve_entity(candidate, candidates_same_type=[], document_context_names=[])
    assert decision.action == "NEW_ENTITY"
    assert decision.entity_id is None
