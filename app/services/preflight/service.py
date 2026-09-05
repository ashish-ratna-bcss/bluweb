from __future__ import annotations

import time

from app.core.config import Settings
from app.services.preflight.dns import check_dns
from app.services.preflight.extraction import try_extract
from app.services.preflight.feeds import check_feeds
from app.services.preflight.html import analyze_html
from app.services.preflight.http import check_http
from app.services.preflight.javascript import assess_javascript_dependency
from app.services.preflight.models import (
    ContentInfo,
    DiscoveryInfo,
    ExtractionAttempt,
    ExtractionConfidence,
    FetchInfo,
    HTMLAnalysisResult,
    PreflightReport,
    RecommendedFetchStrategy,
    RobotsCheckResult,
    SampleInfo,
    SitemapCheckResult,
)
from app.services.preflight.robots import check_robots
from app.services.preflight.scoring import calculate_capability_score
from app.services.preflight.sitemap import check_sitemap
from app.services.security.url_security import URLSecurityError, URLSecurityService


class PreflightService:
    """Orchestrates the pre-flight pipeline: DNS -> HTTP -> robots ->
    sitemap -> feeds -> HTML analysis -> representative sampling ->
    extraction test -> JS/browser fallback -> capability score.

    Bounded by design: at most `preflight_max_sample_pages` additional
    fetches beyond the homepage, and browser rendering is only attempted
    once, only if the deterministic JS-dependency heuristic says it's
    needed. This never crawls the whole site.
    """

    def __init__(self, settings: Settings, security: URLSecurityService):
        self._settings = settings
        self._security = security

    async def run(self, url: str) -> PreflightReport:
        start = time.monotonic()

        try:
            hostname = self._security.validate_scheme(url)
        except URLSecurityError as exc:
            return _failed_report(url, str(exc), start)

        dns_result = await check_dns(hostname, self._security)
        if not dns_result.resolves or dns_result.error:
            return _failed_report(url, dns_result.error or "DNS resolution failed", start)

        http_result, tls_result, response = await check_http(url, self._settings, self._security)
        if not http_result.reachable:
            return _failed_report(url, http_result.error or "site unreachable", start)

        final_url = http_result.final_url or url
        html_text = response.text if response is not None else None

        robots = await check_robots(final_url, self._settings, self._security)
        sitemap = await check_sitemap(final_url, robots, self._settings, self._security)
        feed = await check_feeds(final_url, html_text, self._settings, self._security)

        html_analysis: HTMLAnalysisResult | None = None
        js_assessment = None
        http_only_extraction = ExtractionAttempt(url=final_url, fetched=False, extracted=False)
        extraction_attempts: list[ExtractionAttempt] = []

        if html_text:
            html_analysis = analyze_html(html_text, final_url)
            js_assessment = assess_javascript_dependency(html_text, html_analysis)
            http_only_extraction = try_extract(final_url, html_text)
            extraction_attempts.append(http_only_extraction)
            extraction_attempts.extend(
                await self._sample_pages(final_url, html_analysis, sitemap)
            )

        browser_result = None
        browser_extraction = None
        recommended_strategy = RecommendedFetchStrategy.HTTP
        if js_assessment and js_assessment.likely_requires_browser:
            from app.services.preflight.browser import try_browser_render

            browser_result, browser_extraction = await try_browser_render(
                final_url, http_only_extraction, self._settings, self._security
            )
            if browser_result.succeeded and browser_result.extraction_improved:
                recommended_strategy = RecommendedFetchStrategy.BROWSER
                if browser_extraction:
                    extraction_attempts.append(browser_extraction)

        capability = calculate_capability_score(
            http=http_result,
            robots=robots,
            sitemap=sitemap,
            feed=feed,
            html_analysis=html_analysis,
            extraction_attempts=extraction_attempts,
            js_requires_browser=bool(js_assessment and js_assessment.likely_requires_browser),
            browser_result=browser_result,
            http_only_extracted=http_only_extraction.extracted,
        )

        limitations = _build_limitations(robots, tls_result, browser_result, js_assessment)
        recommendations = _build_recommendations(recommended_strategy, sitemap, feed)

        fetched = [a for a in extraction_attempts if a.fetched]
        extractable = [a for a in fetched if a.extracted]

        report = PreflightReport(
            url=url,
            final_url=final_url,
            status="completed",
            capability=capability,
            discovery=DiscoveryInfo(
                sitemap=sitemap.available,
                rss=feed.available and feed.feed_type == "rss",
                atom=feed.available and feed.feed_type == "atom",
                html_links=bool(html_analysis and html_analysis.internal_links),
                estimated_discoverable_urls=sitemap.estimated_url_count,
            ),
            fetch=FetchInfo(
                http=http_result.reachable,
                browser=bool(browser_result and browser_result.succeeded),
                recommended=recommended_strategy,
            ),
            content=ContentInfo(
                html=bool(http_result.content_type and "html" in http_result.content_type),
                pdf=bool(html_analysis and html_analysis.document_links),
                json=False,
                xml=sitemap.available,
                images=bool(html_analysis and html_analysis.image_count),
            ),
            extraction=_confidence_from_attempts(extraction_attempts),
            sample=SampleInfo(
                tested=len(extraction_attempts),
                fetched=len(fetched),
                extractable=len(extractable),
            ),
            limitations=limitations,
            recommendations=recommendations,
            duration_ms=(time.monotonic() - start) * 1000,
        )
        return report

    async def _sample_pages(
        self,
        final_url: str,
        html_analysis: HTMLAnalysisResult,
        sitemap: SitemapCheckResult,
    ) -> list[ExtractionAttempt]:
        candidates = list(dict.fromkeys([*sitemap.sample_urls, *html_analysis.internal_links]))
        candidates = [u for u in candidates if u != final_url]
        limit = max(0, self._settings.preflight_max_sample_pages - 1)  # homepage already counted
        candidates = candidates[:limit]

        attempts: list[ExtractionAttempt] = []
        async with self._security.build_client(timeout=self._settings.preflight_http_timeout_seconds) as client:
            for candidate_url in candidates:
                try:
                    self._security.validate_scheme(candidate_url)
                    response = await client.get(candidate_url)
                except Exception as exc:  # noqa: BLE001 - a single sample page failing must not abort preflight
                    attempts.append(
                        ExtractionAttempt(url=candidate_url, fetched=False, extracted=False, error=str(exc))
                    )
                    continue

                if response.status_code >= 400 or "html" not in (response.headers.get("content-type") or ""):
                    attempts.append(ExtractionAttempt(url=candidate_url, fetched=True, extracted=False))
                    continue

                attempts.append(try_extract(candidate_url, response.text))

        return attempts


def _confidence_from_attempts(attempts: list[ExtractionAttempt]) -> ExtractionConfidence:
    fetched = [a for a in attempts if a.fetched]
    if not fetched:
        return ExtractionConfidence(title=0.0, author=0.0, date=0.0, body=0.0)
    n = len(fetched)
    return ExtractionConfidence(
        title=round(sum(1 for a in fetched if a.title_found) / n, 2),
        author=round(sum(1 for a in fetched if a.author_found) / n, 2),
        date=round(sum(1 for a in fetched if a.date_found) / n, 2),
        body=round(sum(1 for a in fetched if a.extracted) / n, 2),
    )


def _build_limitations(
    robots: RobotsCheckResult, tls, browser_result, js_assessment
) -> list[str]:
    limitations: list[str] = []
    if robots.exists and not robots.fetch_allowed:
        limitations.append("robots.txt disallows our crawler user agent for this path")
    if not tls.used_https:
        limitations.append("site served over plain HTTP; no transport encryption")
    if js_assessment and js_assessment.likely_requires_browser and browser_result and not browser_result.succeeded:
        limitations.append("page appears to require JavaScript rendering, and the browser fallback failed")
    return limitations


def _build_recommendations(
    recommended_strategy: RecommendedFetchStrategy, sitemap: SitemapCheckResult, feed
) -> list[str]:
    recs = []
    if recommended_strategy == RecommendedFetchStrategy.HTTP:
        recs.append("Use the HTTP crawler for primary collection")
    else:
        recs.append("Use browser rendering as the primary fetch strategy for this site")
    if sitemap.available:
        recs.append("Use the sitemap for efficient URL discovery during monitoring")
    if feed.available:
        recs.append(f"Use the {feed.feed_type} feed for efficient new-content discovery during monitoring")
    return recs


def _failed_report(url: str, error: str, start: float) -> PreflightReport:
    from app.services.preflight.models import CapabilityScore, ConfidenceLevel

    zero_capability = CapabilityScore(
        score=0,
        confidence=ConfidenceLevel.LOW,
        discovery_score=0,
        fetch_score=0,
        extraction_score=0,
        rendering_score=0,
        content_type_score=0,
    )
    return PreflightReport(
        url=url,
        final_url=None,
        status="failed",
        capability=zero_capability,
        discovery=DiscoveryInfo(sitemap=False, rss=False, atom=False, html_links=False, estimated_discoverable_urls=None),
        fetch=FetchInfo(http=False, browser=False, recommended=RecommendedFetchStrategy.HTTP),
        content=ContentInfo(html=False, pdf=False, json=False, xml=False, images=False),
        extraction=ExtractionConfidence(title=0.0, author=0.0, date=0.0, body=0.0),
        sample=SampleInfo(tested=0, fetched=0, extractable=0),
        limitations=[error],
        recommendations=[],
        duration_ms=(time.monotonic() - start) * 1000,
        error=error,
    )
