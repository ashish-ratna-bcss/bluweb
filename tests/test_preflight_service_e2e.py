from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.services.preflight.models import RecommendedFetchStrategy
from app.services.preflight.service import PreflightService
from app.services.security.url_security import URLSecurityService

ARTICLE_HTML = (
    "<html><head><title>A real article</title>"
    '<link rel="canonical" href="https://news.example.com/"/>'
    "</head><body><h1>Headline</h1><p>"
    + ("Real visible article text with enough content to look genuine. " * 30)
    + '</p><a href="/article-2">Next</a></body></html>'
)


class _FakeDNSAnswer:
    def __init__(self, address: str):
        self.address = address


def _mock_transport_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path in ("/", "/article-2"):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=ARTICLE_HTML.encode())
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def _patch_dns(monkeypatch: pytest.MonkeyPatch):
    import dns.asyncresolver
    import dns.exception

    async def fake_resolve(self, hostname, rdtype):
        if rdtype == "A":
            return [_FakeDNSAnswer("93.184.216.34")]
        raise dns.exception.DNSException("no AAAA")

    monkeypatch.setattr(dns.asyncresolver.Resolver, "resolve", fake_resolve)


@pytest.fixture(autouse=True)
def _patch_http_client(monkeypatch: pytest.MonkeyPatch):
    def fake_build_client(self, **kwargs):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(_mock_transport_handler),
            follow_redirects=False,
        )

    monkeypatch.setattr(URLSecurityService, "build_client", fake_build_client)


async def test_preflight_static_site_recommends_http_and_completes():
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", preflight_max_sample_pages=5)
    security = URLSecurityService(settings)
    service = PreflightService(settings, security)

    report = await service.run("https://news.example.com/")

    assert report.status == "completed"
    assert report.fetch.http is True
    assert report.fetch.browser is False
    assert report.fetch.recommended == RecommendedFetchStrategy.HTTP
    assert report.capability.score > 0
    assert report.sample.tested >= 1
    assert report.error is None


async def test_preflight_unreachable_host_fails_cleanly(monkeypatch: pytest.MonkeyPatch):
    def failing_client(self, **kwargs):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    monkeypatch.setattr(URLSecurityService, "build_client", failing_client)

    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    security = URLSecurityService(settings)
    service = PreflightService(settings, security)

    report = await service.run("https://unreachable.example.com/")

    assert report.status == "failed"
    assert report.error is not None
    assert report.capability.score == 0


async def test_preflight_rejects_localhost_before_any_network_call():
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:")
    security = URLSecurityService(settings)
    service = PreflightService(settings, security)

    report = await service.run("http://127.0.0.1:8000/admin")

    assert report.status == "failed"
    assert "blocked" in report.error.lower() or "range" in report.error.lower()
