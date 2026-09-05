"""Single entry point crawl_engine.py calls: classify the page, then route
to the page-type-specific extractor (News/Blog/Forum/Classified) or a
generic fallback shaped the same way otherwise. One extruct call per page
(structured data is computed once here and threaded into every extractor)
and one return shape (`ArticleDocument`) regardless of page type, so
crawl_engine.py doesn't need to branch on extraction result type.

Layer 3 (spec Phase 6 capability 5): if whichever extractor above ran
comes back with nothing, or a body too thin to be useful, one Scrapling
adaptive-DOM attempt is made here -- uniformly, for any page type -- before
giving up. This is the one place that decision is made, rather than
duplicated inside every extractor module.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.classification.page_classifier import ClassificationResult, PageType, classify
from app.services.events import Event, default_bus
from app.services.extraction.adaptive_dom import extract_with_scrapling
from app.services.extraction.article_extractor import ArticleDocument, extract_article
from app.services.extraction.forum_extractor import extract_forum
from app.services.extraction.generic_extractor import extract as generic_extract
from app.services.extraction.index_extractor import extract_index
from app.services.extraction.listing_extractor import extract_listing
from app.services.extraction.provenance import build_provenance
from app.services.extraction.quality_scorer import QualityScore, score_extraction
from app.services.extraction.structured_data import extract_structured_data
from app.services.preflight.models import HTMLAnalysisResult

FORUM_THREAD_TYPES = (PageType.FORUM_THREAD, PageType.DISCUSSION_THREAD)
# Index-shaped page types the generic repeated-item extractor applies to
# (news indexes, forum boards, classifieds, product catalogs, search results).
INDEX_TYPES = (
    PageType.CLASSIFIED_INDEX,
    PageType.SEARCH_RESULTS,
    PageType.CATEGORY,
    PageType.NEWS_INDEX,
    PageType.FORUM_INDEX,
)
MIN_BODY_CHARS_BEFORE_SCRAPLING_FALLBACK = 100
MIN_INDEX_ITEMS = 5  # matches index_extractor.MIN_REPEATED_ITEMS -- below this, an "index" page is probably just a page with a few incidental links
# Quality-triggered fallback (spec Phase 9 sections 15/17): calibrated
# against test_quality_scorer.py's real-fixture cases -- the real BBC
# article scores 0.6+, a deliberately contaminated body (5+ boilerplate
# phrases) pushes contamination_ratio to 1.0. These are the same
# thresholds those tests were written against, not independently guessed.
LOW_QUALITY_THRESHOLD = 0.35
CONTAMINATION_THRESHOLD = 0.6


@dataclass
class PageExtractionResult:
    classification: ClassificationResult
    document: ArticleDocument | None
    quality: QualityScore | None = None


async def extract_for_page(
    url: str, html: str, *,
    html_analysis: HTMLAnalysisResult | None, is_seed_homepage: bool = False, pattern_hint: str | None = None,
) -> PageExtractionResult:
    structured = extract_structured_data(html, url)
    classification = classify(
        url=url, structured=structured, html_analysis=html_analysis,
        is_seed_homepage=is_seed_homepage, html=html, pattern_hint=pattern_hint,
    )
    page_type = classification.page_type
    await default_bus.publish(Event("PAGE_CLASSIFIED", {
        "url": url, "page_type": page_type.value, "confidence": classification.confidence,
    }))
    if any("historical domain/page-pattern" in s for s in classification.signals):
        await default_bus.publish(Event("CLASSIFICATION_CORRECTED", {"url": url, "page_type": page_type.value}))

    if page_type in (PageType.NEWS_ARTICLE, PageType.BLOG_POST):
        document = extract_article(url, html, page_type, structured=structured)
    elif page_type in FORUM_THREAD_TYPES:
        document = extract_forum(url, html, structured=structured)
        if document is not None:
            await default_bus.publish(Event("FORUM_EXTRACTED", {"url": url}))
    elif page_type == PageType.CLASSIFIED_LISTING:
        document = extract_listing(url, html, structured=structured)
        if document is not None:
            await default_bus.publish(Event("LISTING_EXTRACTED", {"url": url}))
    elif page_type in INDEX_TYPES:
        document = _index_to_document(url, html)
        if document is not None:
            await default_bus.publish(Event("INDEX_EXTRACTED", {"url": url, "listing_count": document.raw_metadata.get("listing_count", 0)}))
            if page_type == PageType.CLASSIFIED_INDEX:
                await default_bus.publish(Event("LISTING_EXTRACTED", {"url": url}))
        if document is None and page_type == PageType.FORUM_INDEX:
            # Board pages sometimes look like threads; try forum extractor once.
            document = extract_forum(url, html, structured=structured)
            if document is not None:
                await default_bus.publish(Event("FORUM_EXTRACTED", {"url": url}))
        if document is None:
            document = _generic_fallback(url, html)
    else:
        document = _generic_fallback(url, html)

    quality = score_extraction(document) if document is not None else None
    if quality is not None:
        await default_bus.publish(Event("EXTRACTION_QUALITY_SCORED", {
            "url": url, "overall": quality.overall, "contamination_ratio": quality.contamination_ratio,
        }))
    needs_fallback = (
        document is None
        or len(document.body) < MIN_BODY_CHARS_BEFORE_SCRAPLING_FALLBACK
        or (quality is not None and (quality.overall < LOW_QUALITY_THRESHOLD or quality.contamination_ratio >= CONTAMINATION_THRESHOLD))
    )
    if needs_fallback:
        if quality is not None and document is not None and quality.overall < 1.0:
            await default_bus.publish(Event("LOW_EXTRACTION_QUALITY", {"url": url, "overall": quality.overall, "contamination_ratio": quality.contamination_ratio}))
        await default_bus.publish(Event("EXTRACTION_FALLBACK", {"url": url, "reason": "low_quality_or_thin"}))
        fallback_document = await _scrapling_fallback(url, html, page_type, fallback_document=document)
        fallback_quality = score_extraction(fallback_document) if fallback_document is not None else None
        # Keep whichever result actually scores better -- Scrapling's own
        # generic content-block heuristic isn't guaranteed to beat a
        # page-type extractor that merely tripped the low-quality/
        # contamination threshold (e.g. a short-but-clean listing body).
        if fallback_quality is not None and (quality is None or fallback_quality.overall >= quality.overall):
            document, quality = fallback_document, fallback_quality
        elif document is None:
            document, quality = fallback_document, fallback_quality

    if document is not None:
        provenance = build_provenance(document, structured)
        document.raw_metadata = {**document.raw_metadata, "provenance": provenance}
        await default_bus.publish(Event("PROVENANCE_RECORDED", {"url": url, "field_count": len(provenance) if "_note" not in provenance else 0}))
        await default_bus.publish(Event("EXTRACTION_COMPLETED", {
            "url": url, "extractor": document.extractor, "page_type": page_type.value,
        }))

    return PageExtractionResult(classification=classification, document=document, quality=quality)


async def _scrapling_fallback(
    url: str, html: str, page_type: PageType, *, fallback_document: ArticleDocument | None
) -> ArticleDocument | None:
    scrapled = extract_with_scrapling(html, identifier=f"{page_type.value}:{url.split('?')[0]}")
    await default_bus.publish(Event("SCRAPLING_ATTEMPTED", {"url": url, "success": scrapled is not None}))
    if scrapled is None:
        return fallback_document

    return ArticleDocument(
        extractor="scrapling",
        headline=fallback_document.headline if fallback_document else None,
        body=scrapled.text,
        author=None,
        publisher=None,
        published_at=None,
        updated_at=None,
        section=None,
        canonical_url=url,
        language=None,
        tags=[],
        images=[],
        confidence=scrapled.confidence,
        fields_detected=["body"],
        raw_metadata={"scrapling_selector": scrapled.selector_used},
    )


def _index_to_document(url: str, html: str) -> ArticleDocument | None:
    """CLASSIFIED_INDEX (spec Phase 9 section 18): produces the
    `metadata["listings"]` shape Phase 8's `change_detection.py::
    _detect_index_change` already expects and was built against -- that
    comparator existed since Phase 7/8 with no real extractor feeding it
    (documented then as a known gap); `index_extractor.py`'s generic
    repeated-item detection is what closes it."""
    result = extract_index(html, url)
    if len(result.listings) < MIN_INDEX_ITEMS:
        return None

    listings_metadata = [
        {
            "id": item.item_id, "title": item.title, "url": item.url, "price": item.price,
            "location": item.location, "image": item.image, "summary": item.summary,
            "published_at": item.published_at, "author": item.author, "status": item.status,
        }
        for item in result.listings
    ]
    body = "\n\n".join(f"{item.title}: {item.description}" for item in result.listings[:50] if item.title)
    if len(body) < 20:
        return None

    return ArticleDocument(
        extractor="index",
        headline=None,
        body=body,
        author=None,
        publisher=None,
        published_at=None,
        updated_at=None,
        section=None,
        canonical_url=url,
        language=None,
        tags=[],
        images=[img for item in result.listings if (img := item.image)][:20],
        confidence=0.6,
        fields_detected=["body", "listings"],
        raw_metadata={"listings": listings_metadata, "listing_count": len(result.listings), "truncated": result.truncated},
    )


def _generic_fallback(url: str, html: str) -> ArticleDocument | None:
    """Every other page type (forum/classified/home/category/unknown/...)
    doesn't have a dedicated extractor yet -- give Trafilatura's generic
    extraction a shot anyway (a home page often has a real lede, a category
    page sometimes has real intro text) rather than extracting nothing."""
    generic = generic_extract(url, html)
    if generic is None:
        return None

    fields_detected = ["body"]
    if generic.title:
        fields_detected.append("headline")
    if generic.author:
        fields_detected.append("author")
    if generic.raw_metadata.get("tables"):
        fields_detected.append("tables")
    if generic.raw_metadata.get("lists"):
        fields_detected.append("lists")

    return ArticleDocument(
        extractor="generic",
        headline=generic.title,
        body=generic.text,
        author=generic.author,
        publisher=None,
        published_at=generic.published_at,
        updated_at=None,
        section=None,
        canonical_url=generic.canonical_url or url,
        language=generic.language,
        tags=generic.tags,
        images=[],
        confidence=0.5 if generic.title else 0.3,
        fields_detected=fields_detected,
        raw_metadata=generic.raw_metadata,
    )
