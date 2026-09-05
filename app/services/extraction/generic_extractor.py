"""Layer 1 of the extraction stack: a deterministic, generic extractor
built on Trafilatura. Per spec section 30, this is the extractor every
page falls back to; SourceTypeExtractor/SiteSpecificExtractor plugins
(forum threads, classifieds) can sit in front of it later via the same
`ExtractorPlugin` shape (can_handle/extract) without touching this module.
"""

from __future__ import annotations

from datetime import datetime

import trafilatura

from app.services.extraction.models import ExtractedDocument
from app.services.extraction.list_extractor import extract_lists
from app.services.extraction.table_extractor import extract_tables

MIN_BODY_CHARS = 100


def extract(url: str, html: str) -> ExtractedDocument | None:
    """Returns None if extraction failed or produced too little content to
    be useful -- callers should treat that as "extraction failed", not
    silently store an empty document."""
    try:
        result = trafilatura.bare_extraction(
            html,
            url=url,
            with_metadata=True,
            include_comments=False,
            favor_precision=False,
        )
    except Exception:  # noqa: BLE001 - extraction must never crash the crawl
        return None

    if not result or not result.text or len(result.text) < MIN_BODY_CHARS:
        return None

    tables = extract_tables(html)
    lists = extract_lists(html, base_url=url)
    raw_metadata = {
        "sitename": result.sitename,
        "description": result.description,
        "hostname": result.hostname,
    }
    if tables:
        raw_metadata["tables"] = [
            {
                "headers": t.headers,
                "rows": t.rows,
                "caption": t.caption,
                "header_row_count": t.header_row_count,
            }
            for t in tables
        ]
    if lists:
        raw_metadata["lists"] = [
            {
                "list_type": lst.list_type,
                "heading": lst.heading,
                "role": lst.role,
                "items": [{"text": i.text, "url": i.url} for i in lst.items],
            }
            for lst in lists
        ]

    return ExtractedDocument(
        url=url,
        title=result.title or None,
        author=result.author or None,
        published_at=_parse_date(result.date),
        text=result.text,
        language=result.language or None,
        canonical_url=result.url or None,
        categories=list(result.categories or []),
        tags=list(result.tags or []),
        raw_metadata=raw_metadata,
    )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
