"""English statistical NER via spaCy (spec Phase 8 sections 10-11).

Verified live this session: `en_core_web_sm` correctly tags PERSON/ORG/
DATE on a real sentence ("Narendra Modi" PERSON, "Microsoft Corporation"
ORG, "September 3" DATE); it mistagged "Hyderabad" as ORG rather than the
expected location type on that same sentence -- a real, small-model
accuracy limitation, noted honestly rather than hidden. GLiNER
(gliner_extractor.py) is the intended cross-check/supplement for
LOCATION and the categories spaCy's built-in label set doesn't have at all
(EVENT/PRODUCT/VEHICLE/online handle).

Model loaded once per process (spec section 47: no per-request model
loading) via a module-level lazy singleton.
"""

from __future__ import annotations

import logging

from app.services.intelligence.models import EntityCandidate, EntityType

logger = logging.getLogger("webintel.intelligence.spacy")

_SPACY_LABEL_MAP: dict[str, EntityType] = {
    "PERSON": EntityType.PERSON,
    "ORG": EntityType.ORGANIZATION,
    "GPE": EntityType.LOCATION,
    "LOC": EntityType.LOCATION,
    "DATE": EntityType.DATE,
    "TIME": EntityType.TIME,
    "MONEY": EntityType.MONEY,
}

_nlp = None
_load_attempted = False


def is_available() -> bool:
    return _get_pipeline() is not None


def _get_pipeline():
    global _nlp, _load_attempted
    if _load_attempted:
        return _nlp
    _load_attempted = True
    try:
        import spacy
        _nlp = spacy.load("en_core_web_sm", disable=["parser", "lemmatizer"])
    except Exception:  # noqa: BLE001 - model unavailable must degrade, never crash the crawl
        logger.warning("spaCy en_core_web_sm unavailable; spaCy NER disabled", exc_info=True)
        _nlp = None
    return _nlp


def extract_spacy(text: str) -> list[EntityCandidate]:
    nlp = _get_pipeline()
    if nlp is None:
        return []

    doc = nlp(text[:100_000])  # bounded input, same discipline as generic_extractor.py's MIN/MAX guards
    candidates: list[EntityCandidate] = []
    for ent in doc.ents:
        entity_type = _SPACY_LABEL_MAP.get(ent.label_)
        if entity_type is None:
            continue  # spaCy's default label set includes types outside this system's taxonomy (NORP, LAW, ...) -- skip, not mapped
        candidates.append(EntityCandidate(
            raw_text=ent.text, entity_type=entity_type, confidence=0.75, extractor="spacy",
            start_offset=ent.start_char, end_offset=ent.end_char,
        ))
    return candidates
