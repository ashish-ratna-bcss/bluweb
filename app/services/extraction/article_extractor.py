"""News + Blog extraction (spec Phases C/D).

One implementation for both, per the spec's own instruction ("reuse
NewsExtractor primitives" for blog "do not duplicate code") -- news and
blog posts differ only in `page_type`/`extractor` label, not in what
fields matter or how they're resolved. Layers structured data (JSON-LD/
OpenGraph, phase B) over Trafilatura's own extraction (phase 1's
GenericExtractor, unchanged and still the primary body-text source) rather
than replacing it: structured data wins for fields it actually has
(headline/author/dates/publisher/section/tags/images), Trafilatura fills
whatever's missing, and the body text always comes from Trafilatura.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.services.classification.page_classifier import PageType
from app.services.extraction.generic_extractor import extract as generic_extract
from app.services.extraction.structured_data import StructuredData, extract_structured_data

MIN_CONFIDENCE_FIELDS = 2  # below this many detected fields, don't claim a confident extraction


@dataclass
class ArticleDocument:
    extractor: str  # "news" | "blog"
    headline: str | None
    body: str
    author: str | None
    publisher: str | None
    published_at: datetime | None
    updated_at: datetime | None
    section: str | None
    canonical_url: str | None
    language: str | None
    tags: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    confidence: float = 0.0
    fields_detected: list[str] = field(default_factory=list)
    raw_metadata: dict = field(default_factory=dict)


def extract_article(
    url: str, html: str, page_type: PageType, *, structured: StructuredData | None = None
) -> ArticleDocument | None:
    """Returns None if there's no usable body text at all -- a page with
    perfect structured metadata but no article body isn't an article.

    `structured` lets a caller that already ran `extract_structured_data`
    (e.g. the page classifier, which needs it too) pass the result through
    instead of parsing the same markup with extruct twice.
    """
    generic = generic_extract(url, html)
    if generic is None:
        return None

    if structured is None:
        structured = extract_structured_data(html, url)
    extractor_name = "blog" if page_type == PageType.BLOG_POST else "news"

    headline = structured.headline or generic.title
    author = structured.author or generic.author
    published_at = structured.published_at or generic.published_at
    canonical_url = structured.canonical_url or generic.canonical_url or url

    fields_detected = _detect_fields(structured, generic, headline, author, published_at)
    confidence = _score_confidence(fields_detected, body_chars=len(generic.text))

    return ArticleDocument(
        extractor=extractor_name,
        headline=headline,
        body=generic.text,
        author=author,
        publisher=structured.publisher,
        published_at=published_at,
        updated_at=structured.updated_at,
        section=structured.section,
        canonical_url=canonical_url,
        language=generic.language,
        tags=structured.tags or generic.tags,
        images=structured.images,
        confidence=confidence,
        fields_detected=fields_detected,
        raw_metadata={**generic.raw_metadata, "schema_type": structured.schema_type, "og_type": structured.og_type},
    )


def _detect_fields(
    structured: StructuredData, generic, headline: str | None, author: str | None, published_at
) -> list[str]:
    fields = []
    if headline:
        fields.append("headline")
    if author:
        fields.append("author")
    if published_at:
        fields.append("published_at")
    if structured.publisher:
        fields.append("publisher")
    if structured.section:
        fields.append("section")
    if structured.tags or generic.tags:
        fields.append("tags")
    if structured.images:
        fields.append("images")
    if generic.text:
        fields.append("body")
    return fields


def _score_confidence(fields_detected: list[str], *, body_chars: int) -> float:
    if "body" not in fields_detected or body_chars < 100:
        return 0.0
    # body + headline is the floor for "this looks like a real article";
    # every additional field detected adds a diminishing bonus.
    base = 0.5 if "headline" in fields_detected else 0.3
    bonus = min(0.4, 0.08 * max(0, len(fields_detected) - 2))
    return round(min(0.99, base + bonus), 2)
