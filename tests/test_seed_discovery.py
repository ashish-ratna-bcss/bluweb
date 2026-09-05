from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.services.discovery.seed_discovery import discover_extra_seeds
from app.services.preflight.models import DiscoveryStatus
from app.services.security.url_security import URLSecurityService

SITEMAP_INDEX = """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://news.example.com/sitemap-articles.xml</loc></sitemap>
</sitemapindex>"""

SITEMAP_LEAF = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://news.example.com/article-1</loc></url>
  <url><loc>https://news.example.com/article-2</loc></url>
</urlset>"""


def _mock_transport(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host != "news.example.com":
        return httpx.Response(404)  # blank.example.com: nothing exists anywhere
    if path == "/robots.txt":
        return httpx.Response(404)
    if path == "/sitemap.xml":
        return httpx.Response(200, content=SITEMAP_INDEX.encode(), headers={"content-type": "application/xml"})
    if path == "/sitemap-articles.xml":
        return httpx.Response(200, content=SITEMAP_LEAF.encode(), headers={"content-type": "application/xml"})
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def _patch_http_client(monkeypatch: pytest.MonkeyPatch):
    def fake_build_client(self, **kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(_mock_transport), follow_redirects=False)

    monkeypatch.setattr(URLSecurityService, "build_client", fake_build_client)


async def test_sitemap_index_returns_real_content_urls_not_nested_sitemap_files(settings: Settings, security: URLSecurityService):
    outcome = await discover_extra_seeds("https://news.example.com/", settings, security)

    assert "https://news.example.com/article-1" in outcome.urls
    assert "https://news.example.com/article-2" in outcome.urls
    assert "https://news.example.com/sitemap-articles.xml" not in outcome.urls
    assert outcome.sitemap_status == DiscoveryStatus.AVAILABLE
    assert outcome.sitemap_url_count == 2


async def test_sitemap_not_present_reports_not_present_status(settings: Settings, security: URLSecurityService):
    outcome = await discover_extra_seeds("https://blank.example.com/", settings, security)

    assert outcome.urls == []
    assert outcome.sitemap_status == DiscoveryStatus.NOT_PRESENT
    assert outcome.feed_status == DiscoveryStatus.NOT_PRESENT
