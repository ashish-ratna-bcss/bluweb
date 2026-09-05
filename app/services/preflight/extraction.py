from __future__ import annotations

from app.services.extraction.generic_extractor import extract as generic_extract
from app.services.preflight.models import ExtractionAttempt


def try_extract(url: str, html: str, used_browser: bool = False) -> ExtractionAttempt:
    """Pre-flight's extraction *test* -- delegates the actual Trafilatura
    call to the shared GenericExtractor and reports pass/fail diagnostics.
    The full extracted content itself isn't needed here (pre-flight never
    persists documents); the crawl engine calls the extractor directly."""
    document = generic_extract(url, html)

    if document is None:
        return ExtractionAttempt(url=url, fetched=True, extracted=False, used_browser=used_browser)

    return ExtractionAttempt(
        url=url,
        fetched=True,
        extracted=True,
        title_found=bool(document.title),
        author_found=bool(document.author),
        date_found=bool(document.published_at),
        body_chars=len(document.text),
        used_browser=used_browser,
    )
