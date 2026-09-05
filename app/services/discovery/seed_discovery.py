"""Efficient re-discovery for monitoring crawls (spec section 77): prefer
sitemap/RSS entries over re-crawling the whole homepage every interval.

Reuses the pre-flight engine's own sitemap/robots/feed checks -- they're
already bounded (sampled, short-timeout) exactly because they were built to
run inline in a request; that same shape is what a scheduler tick wants,
so there's no separate "discovery" implementation to keep in sync with the
pre-flight one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import feedparser

from app.core.config import Settings
from app.db.repositories.crawl_repository import CrawlRepository
from app.db.session import AsyncSessionLocal
from app.services.discovery.sitemap_discovery import discover_sitemap_urls
from app.services.events import Event, default_bus
from app.services.normalization.url_normalizer import extract_domain
from app.services.preflight.feeds import check_feeds
from app.services.preflight.models import DiscoveryStatus
from app.services.preflight.robots import check_robots
from app.services.preflight.sitemap import check_sitemap
from app.services.security.url_security import URLSecurityService

logger = logging.getLogger("webintel.discovery")

_MAX_FEED_ENTRIES = 20


@dataclass
class DiscoveryOutcome:
    urls: list[str] = field(default_factory=list)
    sitemap_status: DiscoveryStatus = DiscoveryStatus.UNKNOWN
    feed_status: DiscoveryStatus = DiscoveryStatus.UNKNOWN
    sitemap_url_count: int = 0
    feed_url_count: int = 0


async def discover_extra_seeds(base_url: str, settings: Settings, security: URLSecurityService) -> DiscoveryOutcome:
    """Best-effort: any failure here just means the crawl falls back to
    homepage-only discovery, never blocks the scheduled crawl itself.

    `urls` are actual article/page URLs -- a feed's own XML URL is a
    discovery mechanism, not a page to crawl and run the extractor against,
    so any feed found is fetched and parsed for its entry links here rather
    than being added to the frontier directly. Same for a sitemap *index*:
    its `sample_urls` are nested sitemap FILE urls (e.g. sitemap-news.xml),
    not content pages -- `discover_sitemap_urls` recurses into those and
    returns only real page URLs (spec Phase 9 section 5, bug fixed here).

    `sitemap_status`/`feed_status` (spec Phase 9 section 7) distinguish "not
    present" from "blocked"/"failed"/"invalid" -- see DiscoveryStatus --
    persisted by the caller via `persist_discovery_outcome` so domain
    capability learning knows the difference.
    """
    outcome = DiscoveryOutcome()
    try:
        robots = await check_robots(base_url, settings, security)
        sitemap = await check_sitemap(base_url, robots, settings, security)
        outcome.sitemap_status = sitemap.status
        if sitemap.available and sitemap.sitemap_url:
            sitemap_result = await discover_sitemap_urls(sitemap.sitemap_url, settings, security)
            outcome.urls.extend(u.url for u in sitemap_result.urls)
            outcome.sitemap_url_count = len(sitemap_result.urls)
            await default_bus.publish(Event("SITEMAP_DISCOVERED", {"url": base_url, "count": outcome.sitemap_url_count}))

        feed = await check_feeds(base_url, None, settings, security)
        outcome.feed_status = feed.status
        if feed.available and feed.feed_url:
            feed_urls = await _feed_entry_links(feed.feed_url, settings, security)
            outcome.urls.extend(feed_urls)
            outcome.feed_url_count = len(feed_urls)
            await default_bus.publish(Event("FEED_DISCOVERED", {"url": base_url, "count": outcome.feed_url_count}))
    except Exception:  # noqa: BLE001
        logger.warning("seed discovery failed for %s, falling back to homepage-only", base_url, exc_info=True)

    outcome.urls = list(dict.fromkeys(outcome.urls))
    return outcome


async def persist_discovery_outcome(base_url: str, outcome: DiscoveryOutcome) -> None:
    """Writes the discovery statuses onto the domain's capability row.
    Separate DB session from the crawl itself -- discovery happens once per
    crawl dispatch, before `run_crawl`'s own session/lock lifecycle starts,
    same reasoning as why this is best-effort (a write failure here must
    never block dispatching the actual crawl)."""
    try:
        async with AsyncSessionLocal() as session:
            await CrawlRepository(session).record_discovery_outcome(
                domain=extract_domain(base_url),
                sitemap_status=outcome.sitemap_status.value,
                feed_status=outcome.feed_status.value,
                sitemap_url_count=outcome.sitemap_url_count,
                feed_url_count=outcome.feed_url_count,
            )
    except Exception:  # noqa: BLE001
        logger.warning("failed to persist discovery outcome for %s", base_url, exc_info=True)


async def _feed_entry_links(feed_url: str, settings: Settings, security: URLSecurityService) -> list[str]:
    async with security.build_client(timeout=settings.preflight_http_timeout_seconds) as client:
        response = await client.get(feed_url)
    parsed = feedparser.parse(response.content)
    return [entry.link for entry in parsed.entries[:_MAX_FEED_ENTRIES] if getattr(entry, "link", None)]
