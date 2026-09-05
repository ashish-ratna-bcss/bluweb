"""Structured metadata extraction (spec Phase B): JSON-LD, microdata,
OpenGraph via `extruct` (BSD-licensed, actively maintained by Zyte/
Scrapinghub, does exactly this and only this -- adopted rather than
hand-rolling a second JSON-LD/OG parser next to the presence-only checks
`preflight/html.py` already does for the capability-scoring use case).

This module only *extracts and normalizes* structured data. Deciding what
kind of page it describes is the page classifier's job
(`app/services/classification/page_classifier.py`), which consumes this.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import extruct

logger = logging.getLogger("webintel.structured_data")

# schema.org @type values that mean "this is an article-shaped thing" --
# used by both the classifier and the News/Blog extractors.
ARTICLE_TYPES = frozenset({
    "Article", "NewsArticle", "BlogPosting", "Report", "AnalysisNewsArticle",
    "OpinionNewsArticle", "ReviewNewsArticle", "SocialMediaPosting", "TechArticle",
})


@dataclass
class StructuredData:
    schema_type: str | None = None
    headline: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    publisher: str | None = None
    section: str | None = None
    tags: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    og_type: str | None = None
    og_title: str | None = None
    canonical_url: str | None = None
    raw_json_ld: list[dict] = field(default_factory=list)


def extract_structured_data(html: str, url: str) -> StructuredData:
    try:
        data = extruct.extract(html, base_url=url, syntaxes=["json-ld", "opengraph", "microdata"], uniform=True)
    except Exception:  # noqa: BLE001 - malformed markup must never break the crawl
        logger.warning("structured data extraction failed for %s", url, exc_info=True)
        return StructuredData()

    json_ld_blocks = [b for b in data.get("json-ld", []) if isinstance(b, dict)]
    og_blocks = data.get("opengraph", [])
    microdata_blocks = [b for b in data.get("microdata", []) if isinstance(b, dict)]

    article_block = _pick_article_block(json_ld_blocks) or _pick_article_block(microdata_blocks)
    og = og_blocks[0] if og_blocks else {}

    result = StructuredData(raw_json_ld=json_ld_blocks)

    if article_block:
        props = article_block.get("properties", article_block)  # microdata nests under "properties"
        result.schema_type = _short_type(article_block.get("@type") or article_block.get("type"))
        result.headline = _as_text(props.get("headline") or props.get("name"))
        result.author = _as_text(_author_name(props.get("author")))
        result.published_at = _parse_datetime(props.get("datePublished"))
        result.updated_at = _parse_datetime(props.get("dateModified"))
        result.publisher = _as_text(_org_name(props.get("publisher")))
        result.section = _as_text(props.get("articleSection"))
        result.tags = _as_list(props.get("keywords"))
        result.images = _as_list(props.get("image"))

    result.og_type = og.get("@type") or og.get("og:type")
    result.og_title = og.get("og:title")
    result.canonical_url = og.get("og:url")

    return result


def _pick_article_block(blocks: list[dict]) -> dict | None:
    for block in blocks:
        type_value = _short_type(block.get("@type") or block.get("type"))
        if type_value in ARTICLE_TYPES:
            return block
    return None


def _short_type(type_value) -> str | None:
    if isinstance(type_value, list):
        type_value = type_value[0] if type_value else None
    if not isinstance(type_value, str):
        return None
    return type_value.rsplit("/", 1)[-1]  # microdata gives full schema.org URLs


def _author_name(author_value):
    if isinstance(author_value, list):
        author_value = author_value[0] if author_value else None
    if isinstance(author_value, dict):
        return author_value.get("name") or author_value.get("properties", {}).get("name")
    return author_value


def _org_name(publisher_value):
    if isinstance(publisher_value, dict):
        return publisher_value.get("name") or publisher_value.get("properties", {}).get("name")
    return publisher_value


def _as_text(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value) if value is not None else None


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, str):
        return None
    normalized = value.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.fromisoformat(normalized) if fmt is None else datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
