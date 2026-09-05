"""URL-pattern normalization (spec Phase 6 section 7): domain-level stats
conflate `/news/*` with `/forum/*` with `/listings/*`, which behave
completely differently. This collapses dynamic path segments (numeric
ids, UUIDs, slugs) into placeholders so `/news/12345` and `/news/67890`
aggregate into one `/news/{id}` pattern instead of two singleton rows.

Deliberately conservative: only collapses segments that clearly look
generated (all-digit, UUID-shaped, or long slug-like strings with hyphens/
underscores). A segment like `/category/electronics` stays literal --
collapsing real taxonomy terms would blur genuinely distinct patterns
together.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"^\d+$")
_SLUG_RE = re.compile(r"^[\w]+(?:[-_][\w]+){2,}$")  # 3+ word-ish parts joined by - or _
_ALPHANUM_ID_RE = re.compile(r"^[a-z0-9]{6,}$", re.IGNORECASE)  # e.g. bbc's "c9dwjv96qyqo"


def normalize_url_pattern(url: str) -> str:
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]

    normalized = [_normalize_segment(s) for s in segments]
    path = "/" + "/".join(normalized)
    return f"{parts.scheme}://{parts.netloc}{path}" if parts.scheme else path


def _normalize_segment(segment: str) -> str:
    if _UUID_RE.match(segment):
        return "{uuid}"
    if _NUMERIC_RE.match(segment):
        return "{id}"
    if _SLUG_RE.match(segment):
        return "{slug}"
    # A long opaque alphanumeric token with mixed case/digits (BBC-style
    # article ids, YouTube-style video ids) but not a real English word.
    if _ALPHANUM_ID_RE.match(segment) and any(c.isdigit() for c in segment) and any(c.isalpha() for c in segment):
        return "{id}"
    return segment
