"""GET /domains/{domain}/capabilities (spec Phase 9 section 6). Follows this
repo's existing convention of monkeypatching at the service/repository
method boundary rather than standing up a real DB (see
test_preflight_service_e2e.py's `URLSecurityService.build_client` patches) --
`CrawlRepository`'s own methods are Postgres-only (JSONB, ON CONFLICT, FOR
UPDATE) and can't run against a lightweight test double anyway.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from app.api.v1.domains import get_domain_capabilities
from app.core.config import Settings
from app.db.repositories.crawl_repository import CrawlRepository


@dataclass
class FakeFetchStats:
    domain: str = "example.com"
    http_attempts: int = 0
    http_successes: int = 0
    http_extraction_failures: int = 0
    browser_attempts: int = 0
    browser_successes: int = 0
    avg_http_latency_ms: float = 0.0
    avg_browser_latency_ms: float = 0.0
    failure_counts: dict = field(default_factory=dict)
    preferred_extractor: str | None = None
    js_required_count: int = 0
    sitemap_status: str = "unknown"
    feed_status: str = "unknown"
    sitemap_url_count: int = 0
    feed_url_count: int = 0
    last_observed_at: datetime | None = None
    browser_superior_count: int = 0
    http_superior_count: int = 0
    browser_equivalent_count: int = 0
    avg_completeness: float = 0.0
    completeness_observations: int = 0
    index_children_discovered_total: int = 0
    avg_extraction_quality: float = 0.0
    quality_observations: int = 0


@dataclass
class FakePatternStats:
    page_type: str | None
    extraction_attempts: int = 0
    extraction_successes: int = 0
    avg_extraction_quality: float = 0.0
    quality_observations: int = 0
    pagination_detected_count: int = 0


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url="sqlite+aiosqlite:///:memory:")


def _patch(monkeypatch, stats, patterns):
    async def fake_get_strategy_stats(self, domain):
        return stats

    async def fake_list_pattern_stats(self, domain, *, limit=100):
        return patterns

    monkeypatch.setattr(CrawlRepository, "get_strategy_stats", fake_get_strategy_stats)
    monkeypatch.setattr(CrawlRepository, "list_pattern_stats", fake_list_pattern_stats)


async def test_unknown_domain_returns_404(settings, monkeypatch):
    from app.core.errors import APIError

    _patch(monkeypatch, None, [])
    with pytest.raises(APIError):
        await get_domain_capabilities("never-crawled.example.com", db=None, settings=settings)


async def test_insufficient_observations_reports_insufficient_data_confidence(settings, monkeypatch):
    stats = FakeFetchStats(http_attempts=2, http_successes=2)
    _patch(monkeypatch, stats, [])

    response = await get_domain_capabilities("new.example.com", db=None, settings=settings)

    assert response.confidence == "insufficient_data"
    assert response.observations == 2


async def test_healthy_domain_with_discovery_and_extraction_data(settings, monkeypatch):
    stats = FakeFetchStats(
        http_attempts=100, http_successes=97, js_required_count=6,
        sitemap_status="available", feed_status="not_present", sitemap_url_count=40,
        last_observed_at=datetime.now(timezone.utc),
    )
    patterns = [
        FakePatternStats(page_type="NEWS_ARTICLE", extraction_attempts=80, extraction_successes=77, avg_extraction_quality=0.9, quality_observations=77, pagination_detected_count=0),
        FakePatternStats(page_type="SEARCH_RESULTS", extraction_attempts=10, extraction_successes=9, avg_extraction_quality=0.6, quality_observations=9, pagination_detected_count=9),
    ]
    _patch(monkeypatch, stats, patterns)

    response = await get_domain_capabilities("news.example.com", db=None, settings=settings)

    assert response.confidence in ("high", "medium")
    assert response.discovery.sitemap == "available"
    assert response.discovery.feed == "not_present"
    assert response.discovery.sitemap_url_count == 40
    assert response.discovery.pagination_detected is True
    assert response.discovery.pagination_observations == 9
    assert response.fetch.http_success_rate == 0.97
    assert response.fetch.browser_required_rate == 0.06
    by_type = {e.page_type: e for e in response.extraction}
    assert by_type["NEWS_ARTICLE"].success_rate == round(77 / 80, 4)
    assert by_type["NEWS_ARTICLE"].avg_quality == 0.9
    assert response.recommendation.strategy == "http"


async def test_page_type_with_no_quality_observations_reports_none_not_zero(settings, monkeypatch):
    stats = FakeFetchStats(http_attempts=20, http_successes=20)
    patterns = [FakePatternStats(page_type="FORUM_THREAD", extraction_attempts=15, extraction_successes=15, avg_extraction_quality=0.0, quality_observations=0)]
    _patch(monkeypatch, stats, patterns)

    response = await get_domain_capabilities("forum.example.com", db=None, settings=settings)

    assert response.extraction[0].avg_quality is None
