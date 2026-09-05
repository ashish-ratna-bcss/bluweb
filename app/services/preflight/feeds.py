from __future__ import annotations

from urllib.parse import urljoin

import feedparser
from selectolax.lexbor import LexborHTMLParser

from app.core.config import Settings
from app.services.preflight.models import DiscoveryStatus, FeedCheckResult
from app.services.security.url_security import URLSecurityService

_FEED_MIME_TYPES = {"application/rss+xml", "application/atom+xml", "application/feed+json"}
_COMMON_PATHS = ["/feed", "/feed/", "/rss.xml", "/atom.xml", "/rss"]
_BLOCKED_STATUS_CODES = {403, 429}


def _discover_feed_links(html: str, base_url: str) -> list[str]:
    tree = LexborHTMLParser(html)
    found = []
    for node in tree.css('link[rel="alternate"]'):
        type_attr = (node.attributes.get("type") or "").lower()
        href = node.attributes.get("href")
        if type_attr in _FEED_MIME_TYPES and href:
            found.append(urljoin(base_url, href))
    return found


async def check_feeds(
    final_url: str, html: str | None, settings: Settings, security: URLSecurityService
) -> FeedCheckResult:
    candidates = _discover_feed_links(html, final_url) if html else []
    candidates += [urljoin(final_url, path) for path in _COMMON_PATHS]
    saw_blocked = saw_failed = saw_invalid = False

    async with security.build_client(timeout=settings.preflight_http_timeout_seconds) as client:
        for feed_url in candidates[:5]:
            try:
                response = await client.get(feed_url)
            except Exception:  # noqa: BLE001
                saw_failed = True
                continue
            if response.status_code in _BLOCKED_STATUS_CODES:
                saw_blocked = True
                continue
            if response.status_code >= 400:
                continue

            parsed = feedparser.parse(response.content)
            if parsed.bozo and not parsed.entries:
                saw_invalid = True
                continue
            if not parsed.get("feed") and not parsed.entries:
                continue

            feed_type = "atom" if parsed.version and "atom" in parsed.version else "rss"
            return FeedCheckResult(
                available=True, feed_type=feed_type, feed_url=feed_url,
                sample_item_count=len(parsed.entries), status=DiscoveryStatus.AVAILABLE,
            )

    if saw_blocked:
        status = DiscoveryStatus.BLOCKED
    elif saw_failed:
        status = DiscoveryStatus.FAILED
    elif saw_invalid:
        status = DiscoveryStatus.INVALID
    else:
        status = DiscoveryStatus.NOT_PRESENT
    return FeedCheckResult(available=False, status=status)
