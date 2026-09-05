"""Layer 3 fallback: Scrapling's selector-healing DOM extraction (spec
Phase 6 section on adaptive extraction). Tried only when Trafilatura
(layer 1, generic_extractor.py) and the page-type-specific extractor
(layer 2, article/forum/listing) both come back empty or too thin -- this
does not replace either, it's a last-resort DOM scrape for sites those two
can't handle at all.

`identifier` should be stable per URL-pattern (not per-URL): Scrapling
persists a per-identifier selector->element mapping in its own local
storage and relocates it on the next call via `adaptive=True` even if the
site's markup changed class/tag names, which is the whole point -- reusing
the same identifier across every page matching a pattern is what lets a
handful of successful extractions "heal" future ones instead of
re-guessing selectors from scratch every time.
"""

from __future__ import annotations

from dataclasses import dataclass

from scrapling import Selector

MIN_TEXT_CHARS = 100

# Cheapest/most-common content containers first; first one that yields
# enough text wins. Not exhaustive by design -- this is a fallback for
# pages the real extractors already gave up on, not a replacement for them.
_CONTENT_SELECTORS = (
    "article",
    "main",
    "[role=main]",
    ".post-content",
    ".article-body",
    ".entry-content",
    "#content",
    ".content",
)


@dataclass
class ScraplingResult:
    text: str
    selector_used: str
    confidence: float


def extract_with_scrapling(html: str, *, identifier: str) -> ScraplingResult | None:
    try:
        page = Selector(html, adaptive=True)
    except Exception:  # noqa: BLE001 - fallback extraction must never crash the crawl
        return None

    for sel in _CONTENT_SELECTORS:
        try:
            found = page.css(sel, identifier=f"{identifier}:{sel}", auto_save=True, adaptive=True)
        except Exception:  # noqa: BLE001
            continue
        if not found:
            continue
        text = "\n".join(el.get_all_text(strip=True) for el in found).strip()
        if len(text) >= MIN_TEXT_CHARS:
            # low confidence by construction: no metadata (author/date/etc),
            # just "a plausible content block was found and has real text".
            return ScraplingResult(text=text, selector_used=sel, confidence=0.35)

    return None
