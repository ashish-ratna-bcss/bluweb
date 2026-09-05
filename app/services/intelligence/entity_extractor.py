"""Entity extraction orchestrator (spec Phase 8 sections 9-11): the single
entry point that runs deterministic regex extraction plus language-routed
NER, and returns one flat, deduplicated candidate list with extractor
provenance preserved.

Language routing (spec section 10):
  ENGLISH/UNKNOWN -> spaCy (PERSON/ORG/LOCATION/DATE/TIME/MONEY, cheap)
                      + GLiNER for the labels spaCy doesn't have
                      (EVENT/PRODUCT/VEHICLE/SOCIAL_HANDLE)
  INDIC           -> IndicNER (PERSON/ORG/LOCATION) if available,
                      else GLiNER's full label set as a documented fallback
                      (see indicner_extractor.py)

Never both spaCy and GLiNER predicting the *same* label on the same
document -- that's the "don't run every model on every document" rule
from spec section 10, applied as "don't run every model on every LABEL"
since GLiNER's cost (~400-650ms measured) is real.
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
        # ENGLISH or UNKNOWN: spaCy is the cheap baseline; GLiNER only
        # fills the label gap spaCy's tag set doesn't cover.
        ran_spacy = spacy_available()
        if ran_spacy:
            candidates.extend(extract_spacy(bounded_text))
            extractors_used.append("spacy")
        else:
            extractors_unavailable.append("spacy")

        if ran_gliner:
            candidates.extend(extract_gliner(bounded_text, skip_spacy_covered_labels=ran_spacy))
            extractors_used.append("gliner")
        else:
            extractors_unavailable.append("gliner")

    return ExtractionResult(
        candidates=_reconcile_conflicts(candidates),
        language_code=detection.language_code,
        extractors_used=extractors_used,
        extractors_unavailable=extractors_unavailable,
    )


_EXTRACTOR_PRIORITY = {"regex": 0, "spacy": 1, "indicner": 1, "gliner": 2}


def _reconcile_conflicts(candidates: list[EntityCandidate]) -> list[EntityCandidate]:
    """Cross-extractor sanity check, found necessary by live testing:
    GLiNER running on the labels spaCy doesn't cover can still mistag a
    span spaCy/regex already confidently identified (e.g. GLiNER tagging
    "Microsoft Corporation" PRODUCT at 0.88 when spaCy already has it
    ORGANIZATION at 0.75, or spaCy mistagging the tail of a phone number
    as DATE when regex already captured the whole number as PHONE).

    Raw confidence alone is the wrong tiebreak -- GLiNER's zero-shot
    scores aren't calibrated against spaCy's trained-model scores, so
    "highest confidence wins" let GLiNER's confident wrong answer beat
    spaCy's correct one in testing. Extractor priority is right instead:
    regex (exact pattern match) > spaCy/IndicNER (task-specific trained
    NER) > GLiNER (general-purpose zero-shot, only meant to fill label
    gaps the higher-priority extractors don't cover). Overlap is checked
    by character span, not exact text, since a wrong short span (spaCy's
    "40-1234-5678") can sit entirely inside a correct longer one (regex's
    "+91-40-1234-5678")."""
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
