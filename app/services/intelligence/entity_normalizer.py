"""Entity text normalization (spec Phase 8 section 12). Pure functions,
no DB -- same pattern as fingerprints.py/text_diff.py in the monitoring
package. Two normalization levels on purpose:

`normalize_name` -- case/unicode/whitespace only, used for the entity's
own `normalized_name` and for exact-match lookups. Deliberately does NOT
strip corporate suffixes or fold "Corp"/"Corporation" together: that would
silently conflate distinct legal identities, exactly what section 33 (entity
resolution safety) warns against.

`normalize_for_alias_matching` -- additionally strips a small, explicit
list of common corporate suffixes, used ONLY as a fuzzy-matching aid for
generating alias *candidates* ("Microsoft Corporation" vs "Microsoft
Corp." vs "Microsoft") -- callers must still run the candidate through
context/type validation before merging (entity_resolver.py), matching the
section 12 pipeline: exact normalization -> alias lookup -> pg_trgm/
RapidFuzz candidates -> context/type validation -> merge decision.
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")

# Deliberately short and specific -- not a large NLP suffix-stripping
# gazetteer. Each entry is a corporate/organizational suffix common enough
# in real news/business text to be worth folding for alias candidates.
_ORG_SUFFIXES = (
    "corporation", "corp", "incorporated", "inc", "limited", "ltd",
    "llc", "llp", "plc", "pvt", "private", "company", "co",
)
_ORG_SUFFIX_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(s) for s in _ORG_SUFFIXES) + r")\.?\s*$",
    re.IGNORECASE,
)


def normalize_name(text: str) -> str:
    """Unicode NFKC fold + casefold + whitespace collapse. Same
    "Hyderabad"/"HYDERABAD"/"hyderabad" -> one normalized form the spec's
    own example asks for -- and nothing more aggressive than that."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _WHITESPACE_RE.sub(" ", folded).strip()


def normalize_for_alias_matching(text: str) -> str:
    base = normalize_name(text)
    stripped = _ORG_SUFFIX_RE.sub("", base).strip()
    return stripped or base  # never return empty -- fall back to the unstripped form
