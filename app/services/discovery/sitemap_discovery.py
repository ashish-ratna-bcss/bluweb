"""Full sitemap discovery (spec Phase 9 section 5) -- extends, doesn't
duplicate, `preflight/sitemap.py`'s existence check.

Real bug found and fixed here: `preflight/sitemap.py::check_sitemap`
already detects a sitemap *index* (`is_index=True`) but its caller,
`discovery/seed_discovery.py::discover_extra_seeds`, dumps the index's
`sample_urls` (URLs of *nested sitemap files*, e.g. `sitemap-news.xml`)
straight into the crawl frontier as if they were content pages -- the
scheduler would enqueue and try to extract a sitemap XML file as an
article. This module recurses into a sitemap index and returns only real
content-page URLs; `seed_discovery.py` is updated to call this instead of
inlining sample_urls itself.

Bounded and budgeted throughout (spec section 29): a domain's sitemap
index can reference hundreds of files each with tens of thousands of
URLs -- this stops well before that, since the crawl frontier is bounded
by `max_pages` anyway and there's no reason to fetch more sitemap data
than the frontier can use.
"""

from __future__ import annotations

import gzip
import logging
from dataclasses import dataclass, field
from xml.etree import ElementTree

from app.core.config import Settings
from app.services.security.url_security import URLSecurityService

logger = logging.getLogger("webintel.discovery.sitemap")

_XML_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
MAX_NESTED_SITEMAPS = 10  # a sitemap index can list hundreds of files; only follow the first few
MAX_URLS_PER_SITEMAP = 500
MAX_TOTAL_URLS = 2_000


@dataclass
class SitemapUrl:
    url: str
    lastmod: str | None = None


@dataclass
class SitemapDiscoveryResult:
    urls: list[SitemapUrl] = field(default_factory=list)
    sitemap_files_fetched: int = 0
    truncated: bool = False


async def discover_sitemap_urls(
    sitemap_url: str, settings: Settings, security: URLSecurityService, *, _depth: int = 0,
) -> SitemapDiscoveryResult:
    result = SitemapDiscoveryResult()
    if _depth > 2:  # sitemap indexes pointing to indexes pointing to indexes -- cut the recursion, not a real-world pattern worth chasing
        return result

    root = await _fetch_and_parse(sitemap_url, settings, security)
    if root is None:
        return result
    result.sitemap_files_fetched = 1

    tag = root.tag.split("}")[-1]
    if tag == "sitemapindex":
        nested = [el.text for el in root.findall("sm:sitemap/sm:loc", _XML_NS) if el.text]
        for nested_url in nested[:MAX_NESTED_SITEMAPS]:
            if len(result.urls) >= MAX_TOTAL_URLS:
                result.truncated = True
                break
            child = await discover_sitemap_urls(nested_url, settings, security, _depth=_depth + 1)
            result.urls.extend(child.urls)
            result.sitemap_files_fetched += child.sitemap_files_fetched
            result.truncated = result.truncated or child.truncated
        if len(nested) > MAX_NESTED_SITEMAPS:
            result.truncated = True
        return result

    for url_el in root.findall("sm:url", _XML_NS)[:MAX_URLS_PER_SITEMAP]:
        loc = url_el.find("sm:loc", _XML_NS)
        if loc is None or not loc.text:
            continue
        lastmod_el = url_el.find("sm:lastmod", _XML_NS)
        result.urls.append(SitemapUrl(url=loc.text, lastmod=lastmod_el.text if lastmod_el is not None else None))
    if len(root.findall("sm:url", _XML_NS)) > MAX_URLS_PER_SITEMAP:
        result.truncated = True

    return result


async def _fetch_and_parse(sitemap_url: str, settings: Settings, security: URLSecurityService) -> ElementTree.Element | None:
    try:
        async with security.build_client(timeout=settings.preflight_http_timeout_seconds) as client:
            response = await client.get(sitemap_url)
    except Exception:  # noqa: BLE001 - discovery must never break the crawl
        logger.debug("sitemap fetch failed for %s", sitemap_url, exc_info=True)
        return None

    if response.status_code >= 400:
        return None

    content = response.content
    if sitemap_url.endswith(".gz") or response.headers.get("content-type", "").endswith("gzip"):
        try:
            content = gzip.decompress(content)
        except OSError:
            pass  # not actually gzipped despite the extension/header -- fall through and try parsing raw

    try:
        return ElementTree.fromstring(content)
    except ElementTree.ParseError:
        logger.debug("sitemap XML parse failed for %s", sitemap_url)
        return None
