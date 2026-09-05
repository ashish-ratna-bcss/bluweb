from __future__ import annotations

from app.services.preflight.models import (
    BrowserFallbackResult,
    CapabilityScore,
    ConfidenceLevel,
    ExtractionAttempt,
    FeedCheckResult,
    HTMLAnalysisResult,
    HTTPCheckResult,
    RobotsCheckResult,
    SitemapCheckResult,
)

_WEIGHTS = {
    "discovery": 0.30,
    "fetch": 0.25,
    "extraction": 0.25,
    "rendering": 0.10,
    "content_type": 0.10,
}


def _clamp(value: float) -> int:
    return max(0, min(100, round(value)))


def _discovery_score(
    sitemap: SitemapCheckResult, feed: FeedCheckResult, html: HTMLAnalysisResult | None
) -> int:
    score = 0.0
    if sitemap.available:
        score += 40
    if feed.available:
        score += 30
    if html and html.internal_links:
        score += min(30, len(html.internal_links) / 20 * 30)
    return _clamp(score)


def _fetch_score(http: HTTPCheckResult, robots: RobotsCheckResult) -> int:
    if not http.reachable:
        return 0
    base = 100.0 if http.status_code and 200 <= http.status_code < 300 else 40.0
    if not robots.fetch_allowed:
        base = min(base, 50.0)
    return _clamp(base)


def _extraction_score(attempts: list[ExtractionAttempt]) -> int:
    fetched = [a for a in attempts if a.fetched]
    if not fetched:
        return 0
    success = sum(1 for a in fetched if a.extracted) / len(fetched)
    return _clamp(success * 100)


def _rendering_score(
    js_requires_browser: bool, browser_result: BrowserFallbackResult | None, http_extracted: bool
) -> int:
    if not js_requires_browser and http_extracted:
        return 100
    if browser_result is None:
        return 50 if js_requires_browser else 80
    if not browser_result.succeeded:
        return 30
    return 90 if browser_result.extraction_improved else 70


def _content_type_score(http: HTTPCheckResult) -> int:
    if not http.reachable:
        return 0
    if http.content_type and "text/html" in http.content_type.lower():
        return 100
    if http.content_type:
        return 60
    return 40


def calculate_capability_score(
    *,
    http: HTTPCheckResult,
    robots: RobotsCheckResult,
    sitemap: SitemapCheckResult,
    feed: FeedCheckResult,
    html_analysis: HTMLAnalysisResult | None,
    extraction_attempts: list[ExtractionAttempt],
    js_requires_browser: bool,
    browser_result: BrowserFallbackResult | None,
    http_only_extracted: bool,
) -> CapabilityScore:
    discovery = _discovery_score(sitemap, feed, html_analysis)
    fetch = _fetch_score(http, robots)
    extraction = _extraction_score(extraction_attempts)
    rendering = _rendering_score(js_requires_browser, browser_result, http_only_extracted)
    content_type = _content_type_score(http)

    overall = (
        discovery * _WEIGHTS["discovery"]
        + fetch * _WEIGHTS["fetch"]
        + extraction * _WEIGHTS["extraction"]
        + rendering * _WEIGHTS["rendering"]
        + content_type * _WEIGHTS["content_type"]
    )

    sample_size = len(extraction_attempts)
    has_errors = any(a.error for a in extraction_attempts) or bool(http.error)
    if sample_size >= 8 and not has_errors:
        confidence = ConfidenceLevel.HIGH
    elif sample_size >= 3:
        confidence = ConfidenceLevel.MEDIUM
    else:
        confidence = ConfidenceLevel.LOW

    return CapabilityScore(
        score=_clamp(overall),
        confidence=confidence,
        discovery_score=discovery,
        fetch_score=fetch,
        extraction_score=extraction,
        rendering_score=rendering,
        content_type_score=content_type,
    )
