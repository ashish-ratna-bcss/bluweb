"""HTTP-vs-browser comparison (Phase 8.1 section 5): must pick the result
with the higher extraction *quality*, not merely the longer body -- a
longer browser-rendered body can still be worse (more boilerplate/nav
picked up). Exercises `_extract_with_browser_escalation` directly with a
fake browser fetch, no real browser/network involved.
"""

from __future__ import annotations

from types import SimpleNamespace

import app.services.crawling.crawl_engine as crawl_engine
from app.core.config import Settings
from app.services.crawling.crawl_engine import RunStats, _extract_with_browser_escalation
from app.services.crawling.fetch_router import FetchStrategy
from app.services.crawling.fetchers import PageFetchResult

_CLEAN_BUT_THIN_HTTP_HTML = (
    "<html><head><title>Notice</title></head><body>"
    "<div id='app'></div>"
    "<p>" + ("This short real paragraph explains the notice in plain clean text. " * 2) + "</p>"
    "</body></html>"
)

_CONTAMINATED_BROWSER_HTML = (
    "<html><head><title>Notice</title></head><body>"
    "<p>"
    + (
        "We use cookies to improve your experience. Subscribe to our newsletter for updates. "
        "All rights reserved. Terms of Service apply. Privacy Policy. Advertisement. "
        "Sponsored content below. Please enable javascript to continue. "
    ) * 6
    + "</p></body></html>"
)


async def test_browser_escalation_prefers_higher_quality_not_longer_body(monkeypatch):
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    security = SimpleNamespace()  # escalation path never calls security when browser_fetch is mocked
    stats = RunStats()

    http_result = PageFetchResult(success=True, final_url="https://example.com/notice", html=_CLEAN_BUT_THIN_HTTP_HTML)

    async def fake_browser_fetch_page(url, settings, security, **kwargs):
        return PageFetchResult(success=True, final_url=url, html=_CONTAMINATED_BROWSER_HTML, used_browser=True)

    monkeypatch.setattr(crawl_engine, "browser_fetch_page", fake_browser_fetch_page)

    events = []
    async def capture(event):
        if event.type == "BROWSER_ESCALATION_COMPARED":
            events.append(event)
    monkeypatch.setattr(crawl_engine.default_bus, "publish", capture)

    fetch_result, _, extraction, comparison = await _extract_with_browser_escalation(
        http_result, FetchStrategy.HTTP, settings, security, stats, is_seed_homepage=False,
    )

    assert fetch_result.used_browser is False  # the contaminated browser result must NOT win
    assert extraction.document is not None
    assert "short real paragraph" in extraction.document.body
    assert comparison == "http_superior"
    assert len(events) == 1
    assert events[0].payload["browser_wins"] is False
    assert events[0].payload["original_quality"] is not None
    assert events[0].payload["browser_quality"] is not None
    assert events[0].payload["original_quality"] > events[0].payload["browser_quality"]
