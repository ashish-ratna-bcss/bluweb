"""Deterministic page-type classifier (spec Phase A).

Signal order, cheapest/most-reliable first: URL pattern -> structured data
(schema.org/JSON-LD, already normalized by structured_data.py) -> OpenGraph
-> DOM heuristics (via the existing HTMLAnalysisResult from the pre-flight
engine, reused rather than re-parsing the page a second time). No ML model
-- this is the same "deterministic first, pluggable interface for a model
later" posture as FetchStrategyRouter. `classify()` is the seam: a future
model-backed classifier is a drop-in replacement with the same signature.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from selectolax.lexbor import LexborHTMLParser

from app.services.extraction.structured_data import ARTICLE_TYPES, StructuredData
from app.services.preflight.models import HTMLAnalysisResult


class PageType(StrEnum):
    HOME = "HOME"
    NEWS_ARTICLE = "NEWS_ARTICLE"
    NEWS_INDEX = "NEWS_INDEX"
    BLOG_POST = "BLOG_POST"
    FORUM_INDEX = "FORUM_INDEX"
    FORUM_THREAD = "FORUM_THREAD"
    DISCUSSION_THREAD = "DISCUSSION_THREAD"
    CLASSIFIED_INDEX = "CLASSIFIED_INDEX"
    CLASSIFIED_LISTING = "CLASSIFIED_LISTING"
    CATEGORY = "CATEGORY"
    SEARCH_RESULTS = "SEARCH_RESULTS"
    FEED = "FEED"
    DOCUMENT = "DOCUMENT"
    # Soft-block / access-restriction outcomes (spec section 10/13, set by
    # crawl_engine.py from soft_block_detector.py's verdict -- never
    # produced by classify() itself, which only reasons about content
    # shape, not access/authenticity).
    LOGIN = "LOGIN"
    ERROR = "ERROR"
    CAPTCHA = "CAPTCHA"
    CONSENT_WALL = "CONSENT_WALL"
    JS_SHELL = "JS_SHELL"
    SOFT_BLOCK = "SOFT_BLOCK"
    UNKNOWN = "UNKNOWN"


@dataclass
class ClassificationResult:
    page_type: PageType
    confidence: float
    signals: list[str] = field(default_factory=list)


_FORUM_THREAD_URL_RE = re.compile(r"/(thread|topic|t)[/-]", re.IGNORECASE)
_FORUM_INDEX_URL_RE = re.compile(r"/(forum|forums|board|boards)/?$", re.IGNORECASE)
_CLASSIFIED_LISTING_URL_RE = re.compile(r"/(listing|classified|ad|item)s?/[\w-]+\d", re.IGNORECASE)
_CLASSIFIED_INDEX_URL_RE = re.compile(r"/(listings|classifieds|ads)/?$", re.IGNORECASE)
_NEWS_INDEX_URL_RE = re.compile(r"/(news|blog|articles|stories)/?$", re.IGNORECASE)
_SEARCH_URL_RE = re.compile(r"[?&]q=|/search[/?]", re.IGNORECASE)
_CATEGORY_URL_RE = re.compile(r"/(category|categories|tag|tags|section)/", re.IGNORECASE)
_DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
_PRODUCT_SCHEMA_TYPES = frozenset({"Product", "Offer"})

# Same container class patterns forum_extractor.py's DOM-heuristic path
# looks for -- kept as a second, independent copy rather than importing
# from the extraction layer: classification has to stay a cheap pre-
# extraction check, and a handful of repeated selectors is not worth a
# cross-layer import (classification -> extraction) that doesn't otherwise
# exist. If the two ever drift, forum_extractor.py is authoritative (it
# does the actual extraction; this is just a detection signal).
_FORUM_CONTAINER_SELECTORS = (".comtr", ".comment", ".post", ".message")
_MIN_FORUM_LIKE_CONTAINERS = 5  # a couple of matches could be coincidence (e.g. one blockquote); a handful in a row is real
_FORUM_SCHEMA_TYPES = frozenset({"DiscussionForumPosting", "Comment"})


def classify(
    *,
    url: str,
    structured: StructuredData,
    html_analysis: HTMLAnalysisResult | None,
    is_seed_homepage: bool = False,
    html: str = "",
    pattern_hint: str | None = None,
) -> ClassificationResult:
    """`html` (optional, spec Phase 7 section 31) feeds the forum-DOM
    signal below -- classification otherwise only sees the pre-computed
    `html_analysis` summary, which doesn't carry structural repetition
    counts. `pattern_hint` (optional) is the page type this URL pattern has
    historically resolved to (e.g. from URLPatternStats.preferred_strategy-
    adjacent page_type tracking) -- used ONLY as the last resort before
    UNKNOWN, never overriding a URL/schema/DOM signal that already fired
    (spec section 33: historical data must not override strong current
    evidence)."""
    signals: list[str] = []

    if url.lower().endswith(_DOCUMENT_EXTENSIONS):
        return ClassificationResult(PageType.DOCUMENT, 0.98, ["url extension"])

    if structured.schema_type in ARTICLE_TYPES:
        signals.append(f"schema.org {structured.schema_type}")
        confidence = 0.85
        if structured.headline:
            signals.append("headline")
            confidence += 0.05
        if structured.published_at:
            signals.append("datePublished")
            confidence += 0.05
        if structured.author:
            signals.append("author")
            confidence += 0.03
        # BlogPosting is unambiguous; a generic "Article" is treated as news
        # by default (more common in the wild) unless other signals say blog.
        if structured.schema_type == "BlogPosting":
            page_type = PageType.BLOG_POST
        elif structured.schema_type == "NewsArticle":
            page_type = PageType.NEWS_ARTICLE
        elif structured.schema_type == "Article":
            page_type = PageType.NEWS_ARTICLE
        else:
            page_type = PageType.NEWS_ARTICLE
        return ClassificationResult(page_type, min(confidence, 0.99), signals)

    if structured.schema_type in _PRODUCT_SCHEMA_TYPES or _product_schema_present(structured):
        signals.append(f"schema.org {structured.schema_type or 'Product/Offer'}")
        return ClassificationResult(PageType.CLASSIFIED_LISTING, 0.75, signals)

    if structured.og_type == "article":
        signals.append("og:type=article")
        return ClassificationResult(PageType.NEWS_ARTICLE, 0.6, signals)

    if is_seed_homepage:
        signals.append("crawl seed / homepage")
        return ClassificationResult(PageType.HOME, 0.9, signals)

    if _FORUM_THREAD_URL_RE.search(url):
        signals.append("URL pattern: thread/topic")
        return ClassificationResult(PageType.FORUM_THREAD, 0.6, signals)
    if _FORUM_INDEX_URL_RE.search(url):
        signals.append("URL pattern: forum index")
        return ClassificationResult(PageType.FORUM_INDEX, 0.55, signals)
    if _CLASSIFIED_LISTING_URL_RE.search(url):
        signals.append("URL pattern: classified listing")
        return ClassificationResult(PageType.CLASSIFIED_LISTING, 0.55, signals)
    if _CLASSIFIED_INDEX_URL_RE.search(url):
        signals.append("URL pattern: classifieds index")
        return ClassificationResult(PageType.CLASSIFIED_INDEX, 0.55, signals)
    if _NEWS_INDEX_URL_RE.search(url):
        signals.append("URL pattern: news/blog index")
        return ClassificationResult(PageType.NEWS_INDEX, 0.55, signals)
    if _SEARCH_URL_RE.search(url):
        signals.append("URL pattern: search")
        return ClassificationResult(PageType.SEARCH_RESULTS, 0.7, signals)
    if _CATEGORY_URL_RE.search(url):
        signals.append("URL pattern: category/tag")
        return ClassificationResult(PageType.CATEGORY, 0.6, signals)

    # Combined forum signal (spec section 31): DOM structure + structured
    # metadata, checked BEFORE the generic long-text-with-heading heuristic
    # below -- a real discussion thread also has long text and headings, so
    # the more specific forum signal has to win the tie, not lose to it.
    forum_container_count = _forum_dom_signal(html)
    forum_schema_present = _forum_schema_signal(structured)
    if forum_container_count >= _MIN_FORUM_LIKE_CONTAINERS or forum_schema_present:
        confidence = 0.5
        if forum_container_count >= _MIN_FORUM_LIKE_CONTAINERS:
            signals.append(f"forum-like DOM structure ({forum_container_count} comment-like containers)")
            confidence += min(0.2, forum_container_count / 500)
        if forum_schema_present:
            signals.append("DiscussionForumPosting/Comment structured data")
            confidence += 0.25
        if pattern_hint == PageType.FORUM_THREAD.value:
            signals.append("historical domain/page-pattern: forum")
            confidence += 0.05
        return ClassificationResult(PageType.FORUM_THREAD, min(confidence, 0.95), signals)

    if html_analysis is not None:
        if html_analysis.meaningful_text_length > 800 and html_analysis.heading_count >= 1:
            signals.append("long-form text with heading (DOM heuristic)")
            return ClassificationResult(PageType.NEWS_ARTICLE, 0.4, signals)

    # Historical pattern hint: last resort before UNKNOWN, never above.
    # Deliberately low confidence -- this is "this URL shape has usually
    # been X before", not current-page evidence.
    if pattern_hint and pattern_hint != PageType.UNKNOWN.value:
        signals.append(f"historical domain/page-pattern: {pattern_hint}")
        try:
            return ClassificationResult(PageType(pattern_hint), 0.35, signals)
        except ValueError:
            pass

    signals.append("no confident signal")
    return ClassificationResult(PageType.UNKNOWN, 0.0, signals)


def _forum_dom_signal(html: str) -> int:
    if not html:
        return 0
    try:
        tree = LexborHTMLParser(html)
        return max((len(tree.css(sel)) for sel in _FORUM_CONTAINER_SELECTORS), default=0)
    except Exception:  # noqa: BLE001 - classification must never crash the crawl
        return 0


def _forum_schema_signal(structured: StructuredData) -> bool:
    for block in structured.raw_json_ld:
        type_value = block.get("@type")
        if isinstance(type_value, list):
            type_value = type_value[0] if type_value else None
        if type_value in _FORUM_SCHEMA_TYPES:
            return True
    return False


def _product_schema_present(structured: StructuredData) -> bool:
    for block in structured.raw_json_ld:
        type_value = block.get("@type")
        if isinstance(type_value, list):
            type_value = type_value[0] if type_value else None
        if type_value in _PRODUCT_SCHEMA_TYPES:
            return True
    return False
