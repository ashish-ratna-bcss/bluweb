"""Phase 9 multi-site validation (spec section 9). Real content fetched
once via curl from public sites (2026-09-05) and committed as fixtures --
same "real content, replayed offline" convention as
`tests/test_live_verification.py`'s BBC/HN/Craigslist fixtures. Labeled
FIXTURE VERIFIED (real internet content, replayed through the pipeline
offline) throughout the Phase 9 report, never "live verified" -- no network
call happens when this test runs.

Sources:
  blog_simonwillison_post.html   -- https://simonwillison.net/2026/Sep/4/astra-pelicans/
  sitemap_simonwillison.xml      -- https://simonwillison.net/sitemap.xml (real: 16,933 <url> entries)
  feed_simonwillison_atom.xml    -- https://simonwillison.net/atom/everything/
  paginated_hn_news_index.html   -- https://news.ycombinator.com/news (real rel="next" pagination)
  generic_python_org_home.html   -- https://www.python.org/
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.services.discovery.pagination import detect_pagination
from app.services.discovery.sitemap_discovery import MAX_URLS_PER_SITEMAP, discover_sitemap_urls
from app.services.extraction.extraction_router import extract_for_page
from app.services.preflight.feeds import check_feeds
from app.services.preflight.html import analyze_html
from app.services.preflight.models import DiscoveryStatus
from app.services.security.url_security import URLSecurityService

_FIXTURES = Path(__file__).parent / "fixtures"


async def test_real_blog_post_classified_and_extracted_correctly():
    html = (_FIXTURES / "blog_simonwillison_post.html").read_text()
    url = "https://simonwillison.net/2026/Sep/4/astra-pelicans/"
    analysis = analyze_html(html, url)

    result = await extract_for_page(url, html, html_analysis=analysis)

    assert result.classification.page_type.value == "NEWS_ARTICLE"  # og:type=article
    assert result.document is not None
    assert result.document.headline == "The Pelican comparison grid for Astra is pretty interesting"
    assert result.document.author == "Simon Willison"
    assert result.document.published_at is not None
    assert result.quality.overall > 0.7
    provenance = result.document.raw_metadata["provenance"]
    assert provenance["body"]["source"] == "trafilatura"
    assert provenance["title"]["value"] == result.document.headline


async def test_real_hn_index_page_pagination_detected_via_rel_next():
    html = (_FIXTURES / "paginated_hn_news_index.html").read_text()

    info = detect_pagination(html, "https://news.ycombinator.com/news")

    assert info.method == "rel_next"
    assert info.next_url == "https://news.ycombinator.com/news?p=2"


async def test_real_hn_index_page_uses_index_extractor_via_news_index_type():
    """Final completion closed the Phase 9 gap: `/news` URL pattern maps to
    NEWS_INDEX, which is in INDEX_TYPES, so the repeated-item extractor runs
    and produces listings[] instead of falling through to Trafilatura-only."""
    html = (_FIXTURES / "paginated_hn_news_index.html").read_text()
    url = "https://news.ycombinator.com/news"
    analysis = analyze_html(html, url)

    result = await extract_for_page(url, html, html_analysis=analysis)

    assert result.classification.page_type.value == "NEWS_INDEX"
    assert result.document is not None
    assert result.document.extractor == "index"
    listings = (result.document.raw_metadata or {}).get("listings") or []
    assert len(listings) >= 5
    assert any(item.get("url") for item in listings)


async def test_real_generic_homepage_extracts_something_useful():
    html = (_FIXTURES / "generic_python_org_home.html").read_text()
    url = "https://www.python.org/"
    analysis = analyze_html(html, url)

    result = await extract_for_page(url, html, html_analysis=analysis)

    assert result.document is not None
    assert result.document.body
    assert result.quality.overall > 0.0


async def test_real_large_sitemap_is_parsed_and_capped_at_max_urls_per_sitemap(monkeypatch: pytest.MonkeyPatch):
    """Real-world scale check: simonwillison.net's actual sitemap has 16,933
    <url> entries in one file (not synthetic) -- proves MAX_URLS_PER_SITEMAP
    genuinely triggers against real data, not just a hand-built fixture."""
    sitemap_xml = (_FIXTURES / "sitemap_simonwillison.xml").read_bytes()

    def fake_build_client(self, **kwargs):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=sitemap_xml, headers={"content-type": "application/xml"})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    monkeypatch.setattr(URLSecurityService, "build_client", fake_build_client)
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    security = URLSecurityService(settings)

    result = await discover_sitemap_urls("https://simonwillison.net/sitemap.xml", settings, security)

    assert len(result.urls) == MAX_URLS_PER_SITEMAP
    assert result.truncated is True
    assert result.urls[0].url == "https://simonwillison.net/2026/Sep/4/astra-pelicans/"


async def test_real_atom_feed_detected_and_parsed(monkeypatch: pytest.MonkeyPatch):
    atom_xml = (_FIXTURES / "feed_simonwillison_atom.xml").read_bytes()

    def fake_build_client(self, **kwargs):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/atom/everything/":
                return httpx.Response(200, content=atom_xml, headers={"content-type": "application/atom+xml"})
            return httpx.Response(404)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    monkeypatch.setattr(URLSecurityService, "build_client", fake_build_client)
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    security = URLSecurityService(settings)

    # check_feeds only tries the first 5 of a fixed common-paths list plus
    # HTML-discovered links; simonwillison.net's real feed lives at a path
    # not in that list, so point it there directly (prepended, since only
    # the first 5 candidates are tried) the same way an HTML
    # <link rel="alternate"> discovery would.
    monkeypatch.setattr("app.services.preflight.feeds._COMMON_PATHS", ["/atom/everything/"])

    result = await check_feeds("https://simonwillison.net/", None, settings, security)

    assert result.status == DiscoveryStatus.AVAILABLE
    assert result.feed_type == "atom"
    assert result.sample_item_count and result.sample_item_count > 0
