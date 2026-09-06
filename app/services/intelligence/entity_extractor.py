"""Entity extraction orchestrator (spec Phase 8 sections 9-11): the single
entry point that runs deterministic regex extraction plus language-routed
NER, and returns one flat, deduplicated candidate list with extractor
provenance preserved.

Language routing:
  ENGLISH/UNKNOWN -> GLiNER primary (full controlled label set)
                      spaCy only if GLiNER is unavailable
  INDIC           -> IndicNER (PERSON/ORG/LOCATION) if available,
                      else GLiNER's full label set as fallback

Never run spaCy and GLiNER on the same document when GLiNER succeeds --
GLiNER already covers the spaCy label set plus EVENT/PRODUCT/VEHICLE/
SOCIAL_HANDLE.
"""

from __future__ import annotations

from app.services.intelligence.deterministic_extractor import extract_deterministic
from app.services.intelligence.gliner_extractor import extract_gliner
from app.services.intelligence.gliner_extractor import is_available as gliner_available
from app.services.intelligence.indicner_extractor import extract_indicner
from app.services.intelligence.indicner_extractor import is_available as indicner_available
from app.services.intelligence.language import LanguageRoute, detect_language
from app.services.intelligence.models import EntityCandidate, ExtractionResult
from app.services.intelligence.spacy_extractor import extract_spacy
from app.services.intelligence.spacy_extractor import is_available as spacy_available

MAX_TEXT_CHARS_FOR_NER = 20_000  # bounded input -- statistical models must never see an unbounded document


def extract_entities(text: str) -> ExtractionResult:
    if not text or not text.strip():
        return ExtractionResult()

    bounded_text = text[:MAX_TEXT_CHARS_FOR_NER]
    detection = detect_language(bounded_text)

    candidates: list[EntityCandidate] = list(extract_deterministic(bounded_text))
    extractors_used = ["regex"]
    extractors_unavailable: list[str] = []

    ran_gliner = gliner_available()

    if detection.route == LanguageRoute.INDIC:
        if indicner_available():
            candidates.extend(extract_indicner(bounded_text))
            extractors_used.append("indicner")
        else:
            extractors_unavailable.append("indicner")
            if ran_gliner:
                candidates.extend(extract_gliner(bounded_text, skip_spacy_covered_labels=False))
                extractors_used.append("gliner")
            else:
                extractors_unavailable.append("gliner")
    else:
        # ENGLISH / UNKNOWN: GLiNER is primary; spaCy only as degradation path.
        if ran_gliner:
            candidates.extend(extract_gliner(bounded_text, skip_spacy_covered_labels=False))
            extractors_used.append("gliner")
        else:
            extractors_unavailable.append("gliner")
            ran_spacy = spacy_available()
            if ran_spacy:
                candidates.extend(extract_spacy(bounded_text))
                extractors_used.append("spacy")
            else:
                extractors_unavailable.append("spacy")

    return ExtractionResult(
        candidates=_reconcile_conflicts(candidates),
        language_code=detection.language_code,
        extractors_used=extractors_used,
        extractors_unavailable=extractors_unavailable,
    )


# GLiNER is primary for English; spaCy is fallback-only, so lower priority.
_EXTRACTOR_PRIORITY = {"regex": 0, "gliner": 1, "indicner": 1, "spacy": 2}


def _reconcile_conflicts(candidates: list[EntityCandidate]) -> list[EntityCandidate]:
    """Prefer higher-priority extractors on overlapping spans.

    regex (exact) > gliner/indicner (primary NER) > spacy (fallback).
    Overlap is by character span so a short wrong span cannot beat a
    longer correct one that contains it.
    """
    ordered = sorted(
        candidates,
        key=lambda c: (_EXTRACTOR_PRIORITY.get(c.extractor, 3), -c.confidence),
    )

    accepted: list[EntityCandidate] = []
    accepted_spans: list[tuple[int, int]] = []
    seen_text: set[str] = set()

    for candidate in ordered:
        text_key = candidate.raw_text.strip().casefold()
        if text_key in seen_text:
            continue

        has_span = candidate.start_offset >= 0 and candidate.end_offset is not None
        if has_span and any(
            candidate.start_offset < end and start < candidate.end_offset
            for start, end in accepted_spans
        ):
            continue

        accepted.append(candidate)
        seen_text.add(text_key)
        if has_span:
            accepted_spans.append((candidate.start_offset, candidate.end_offset))

    return accepted
