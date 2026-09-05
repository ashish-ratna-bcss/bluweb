"""Cheap language detection for NER routing (spec Phase 8 section 10).

`langdetect` (Apache-2.0, a Python port of Google's language-detection
library) -- verified live this session against real English/Hindi/Telugu
sentences before adoption. Deliberately not `fasttext` (needs a ~130MB
binary model download) or a full langid pipeline: this only has to answer
"which NER backend should see this text," not identify language with
research-grade precision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from langdetect import DetectorFactory, LangDetectException, detect

# langdetect's detector is non-deterministic per-process unless seeded --
# same fix used by every project that calls it in a service context.
DetectorFactory.seed = 0

MIN_TEXT_CHARS = 20  # below this, detection is unreliable; treat as unknown


class LanguageRoute(StrEnum):
    ENGLISH = "english"
    INDIC = "indic"
    UNKNOWN = "unknown"


# ISO 639-1 codes langdetect can return for the Indian languages this
# system's own spec names (Hindi, Telugu, Tamil, Kannada, Malayalam,
# Marathi, Bengali, Urdu) -- langdetect distinguishes script, not dialect,
# so this list is exhaustive for those languages' native scripts.
_INDIC_CODES = frozenset({"hi", "te", "ta", "kn", "ml", "mr", "bn", "ur", "gu", "pa", "or", "as"})


@dataclass
class LanguageDetection:
    language_code: str | None  # ISO 639-1, or None if undetected
    route: LanguageRoute


def detect_language(text: str) -> LanguageDetection:
    """Never raises -- undetectable/too-short text routes to UNKNOWN,
    which callers treat as "run the English-capable pipeline" (spec
    section 46: NLP failure must degrade gracefully, never break the
    pipeline)."""
    if len(text.strip()) < MIN_TEXT_CHARS:
        return LanguageDetection(language_code=None, route=LanguageRoute.UNKNOWN)

    try:
        code = detect(text)
    except LangDetectException:
        return LanguageDetection(language_code=None, route=LanguageRoute.UNKNOWN)

    if code in _INDIC_CODES:
        return LanguageDetection(language_code=code, route=LanguageRoute.INDIC)
    return LanguageDetection(language_code=code, route=LanguageRoute.ENGLISH)
