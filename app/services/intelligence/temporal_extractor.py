"""Temporal intelligence (spec Phase 8 section 15). Deliberately thin: no
event-coreference engine, just normalization on top of what's already
available, in the documented preference order --

  1. existing structured metadata (Document.published_at/updated_at,
     already populated by Phase 1-7's extractors from schema.org/OpenGraph
     -- this module does not re-derive those, only consumes them)
  2. explicit/relative in-text date expressions (dateparser, BSD license,
     verified live this session: correctly resolves both an absolute date
     ("September 3rd, 2026") and a relative one ("3 hours ago") in the
     same call)

`dateparser.search.search_dates` is the whole implementation -- no HeidelTime/
SUTime/Duckling (all JVM- or Haskell-based, wrong runtime for this stack;
see the Phase 8 research report section 12).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from dateparser.search import search_dates

MAX_MENTIONS = 10  # bounded -- a long article can have dozens of incidental dates; keep the signal, not all of it
MAX_TEXT_CHARS = 5_000  # dateparser's search is not cheap on very long text


@dataclass
class TemporalMention:
    raw_text: str
    resolved_at: datetime


def extract_temporal_mentions(text: str, *, reference_date: datetime | None = None) -> list[TemporalMention]:
    if not text or not text.strip():
        return []

    settings = {"RETURN_AS_TIMEZONE_AWARE": True, "TIMEZONE": "UTC", "TO_TIMEZONE": "UTC"}
    if reference_date is not None:
        settings["RELATIVE_BASE"] = reference_date.replace(tzinfo=None)

    try:
        found = search_dates(text[:MAX_TEXT_CHARS], languages=["en"], settings=settings)
    except Exception:  # noqa: BLE001 - malformed text must never break extraction
        return []

    if not found:
        return []

    mentions = [
        TemporalMention(raw_text=raw, resolved_at=_ensure_aware(resolved))
        for raw, resolved in found
    ]
    return mentions[:MAX_MENTIONS]


def resolve_document_time(
    *, published_at: datetime | None, updated_at: datetime | None, text_mentions: list[TemporalMention],
) -> datetime | None:
    """Preference order per spec section 15: structured metadata first,
    then the earliest in-text mention as a last-resort signal (an
    article's own body rarely states a "more authoritative" time than its
    own metadata already gives -- text mentions exist mainly to catch
    documents where metadata extraction found nothing)."""
    if updated_at is not None:
        return updated_at
    if published_at is not None:
        return published_at
    if text_mentions:
        return min(m.resolved_at for m in text_mentions)
    return None


def _ensure_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
