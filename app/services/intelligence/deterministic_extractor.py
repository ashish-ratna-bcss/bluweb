"""Deterministic entity extraction (spec Phase 8 section 9): PHONE, EMAIL,
URL, MONEY. Plain regex, no model -- these have unambiguous surface forms
and a statistical model can only get them wrong (hallucinate a phone
number, miss a real one). DATE/TIME are handled separately by
temporal_extractor.py (dateparser), which needs more context than a single
regex pattern can safely give.

Not built on spaCy's EntityRuler despite the spec naming it explicitly --
these patterns don't need spaCy's tokenizer or matcher machinery, and a
blank `re` module avoids loading a spaCy pipeline just to run four regexes
that don't touch spaCy's actual value (statistical NER, done separately in
spacy_extractor.py). Trading spec-letter for spec-intent: the requirement
is "deterministic extraction, no hallucination," not "use EntityRuler
specifically."
"""

from __future__ import annotations

import re

from app.services.intelligence.models import EntityCandidate, EntityType

# Conservative on purpose: false positives here poison entity resolution
# (Section 33's safety requirement) more than false negatives do -- a
# missed phone number is just not extracted; a wrong one is a bad merge key.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL_RE = re.compile(r"\bhttps?://[^\s<>\"']+")

# International-ish phone: optional +country, then 7-14 digits with
# optional separators. Requires at least 7 digits to avoid matching things
# like "at 5-10" or a bare short number. This is intentionally not a full
# libphonenumber-grade validator (out of scope) -- it flags plausible
# phone-shaped strings for the resolver to key on, same spirit as the
# rest of this phase's "cheap deterministic signal" layers.
_PHONE_RE = re.compile(
    r"(?<!\w)(\+?\d{1,3}[-.\s]?)?(\(?\d{2,5}\)?[-.\s]?){2,4}\d{3,4}(?!\w)"
)
_PHONE_MIN_DIGITS = 7

# ₹/Rs/INR/$ + digits (spec's own classified example: "₹65,000"), or a
# bare number followed by a currency word.
_MONEY_RE = re.compile(
    r"(?:₹|Rs\.?|INR|\$|USD)\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:rupees|dollars|USD|INR)",
    re.IGNORECASE,
)


def extract_deterministic(text: str) -> list[EntityCandidate]:
    candidates: list[EntityCandidate] = []

    for m in _EMAIL_RE.finditer(text):
        candidates.append(_candidate(m, EntityType.EMAIL, confidence=0.95))

    for m in _URL_RE.finditer(text):
        candidates.append(_candidate(m, EntityType.URL, confidence=0.95))

    for m in _MONEY_RE.finditer(text):
        candidates.append(_candidate(m, EntityType.MONEY, confidence=0.85))

    for m in _PHONE_RE.finditer(text):
        digit_count = sum(c.isdigit() for c in m.group(0))
        if digit_count < _PHONE_MIN_DIGITS:
            continue
        candidates.append(_candidate(m, EntityType.PHONE, confidence=0.7))

    return candidates


def _candidate(match: re.Match, entity_type: EntityType, *, confidence: float) -> EntityCandidate:
    raw = match.group(0).strip()
    return EntityCandidate(
        raw_text=raw, entity_type=entity_type, confidence=confidence, extractor="regex",
        start_offset=match.start(), end_offset=match.end(),
    )
