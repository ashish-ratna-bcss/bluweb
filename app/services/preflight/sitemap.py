from __future__ import annotations

from urllib.parse import urljoin
from xml.etree import ElementTree

from app.core.config import Settings
from app.services.preflight.models import DiscoveryStatus, RobotsCheckResult, SitemapCheckResult
from app.services.security.url_security import URLSecurityService

_XML_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_SAMPLE_LIMIT = 20
_BLOCKED_STATUS_CODES = {403, 429}


async def check_sitemap(
    final_url: str,
    robots: RobotsCheckResult,
    settings: Settings,
    security: URLSecurityService,
) -> SitemapCheckResult:
    candidates = list(robots.sitemap_urls) or [urljoin(final_url, "/sitemap.xml")]
    # Tracks the worst-case reason no candidate panned out, so a genuine
    # "no sitemap here" (NOT_PRESENT) isn't reported the same way as "we got
    # blocked" (BLOCKED) or "the network hiccuped" (FAILED) -- priority
    # order below matches how informative each signal is for capability
    # learning (a block is worth remembering more than a plain 404).
    saw_blocked = saw_failed = saw_not_present = False

    async with security.build_client(timeout=settings.preflight_http_timeout_seconds) as client:
        for sitemap_url in candidates[:3]:
            try:
                response = await client.get(sitemap_url)
            except Exception:  # noqa: BLE001
                saw_failed = True
                continue
            if response.status_code in _BLOCKED_STATUS_CODES:
                saw_blocked = True
                continue
            if response.status_code >= 400:
                saw_not_present = True
                continue

            try:
                root = ElementTree.fromstring(response.content)
            except ElementTree.ParseError as exc:
                return SitemapCheckResult(available=False, error=f"invalid XML: {exc}", status=DiscoveryStatus.INVALID)

            tag = root.tag.split("}")[-1]
            if tag == "sitemapindex":
                nested = [el.text for el in root.findall("sm:sitemap/sm:loc", _XML_NS) if el.text]
                return SitemapCheckResult(
                    available=True,
                    sitemap_url=sitemap_url,
                    is_index=True,
                    estimated_url_count=None,
                    sample_urls=nested[:_SAMPLE_LIMIT],
                    status=DiscoveryStatus.AVAILABLE,
                )

            urls = [el.text for el in root.findall("sm:url/sm:loc", _XML_NS) if el.text]
            return SitemapCheckResult(
                available=True,
                sitemap_url=sitemap_url,
                is_index=False,
                estimated_url_count=len(urls),
                sample_urls=urls[:_SAMPLE_LIMIT],
                status=DiscoveryStatus.AVAILABLE,
            )

    if saw_blocked:
        status = DiscoveryStatus.BLOCKED
    elif saw_failed:
        status = DiscoveryStatus.FAILED
    elif saw_not_present:
        status = DiscoveryStatus.NOT_PRESENT
    else:
        status = DiscoveryStatus.NOT_PRESENT  # no candidates at all is functionally the same as none found
    return SitemapCheckResult(available=False, status=status)
