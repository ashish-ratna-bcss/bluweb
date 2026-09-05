from __future__ import annotations

import time

from playwright.async_api import Route, async_playwright

from app.core.config import Settings
from app.services.preflight.extraction import try_extract
from app.services.preflight.models import BrowserFallbackResult, ExtractionAttempt
from app.services.security.url_security import URLSecurityError, URLSecurityService


async def _guarded_route(route: Route, security: URLSecurityService) -> None:
    """Validate every request Playwright makes (main navigation AND every
    subresource) against the SSRF policy before letting it proceed.

    Residual: this approves-then-lets-Playwright-connect, it does not pin
    the IP the way _SSRFSafeTransport does for httpx -- a DNS-rebinding
    attacker could still swap the answer between our check and Playwright's
    own connect.
    """
    try:
        hostname = security.validate_scheme(route.request.url)
        await security.resolve_and_validate(hostname)
    except URLSecurityError:
        await route.abort()
        return
    await route.continue_()


async def try_browser_render(
    url: str,
    http_only_extraction: ExtractionAttempt,
    settings: Settings,
    security: URLSecurityService,
) -> tuple[BrowserFallbackResult, ExtractionAttempt | None]:
    start = time.monotonic()
    timeout_ms = settings.preflight_browser_timeout_seconds * 1000

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(user_agent=settings.crawler_user_agent)
                page = await context.new_page()
                await page.route("**/*", lambda route: _guarded_route(route, security))
                await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("networkidle", timeout=min(5000, timeout_ms))
                except Exception:  # noqa: BLE001
                    await page.wait_for_timeout(400)
                rendered_html = await page.content()
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 - browser failures are a diagnostic outcome, not a crash
        return (
            BrowserFallbackResult(
                attempted=True,
                succeeded=False,
                render_duration_ms=(time.monotonic() - start) * 1000,
                error=str(exc),
            ),
            None,
        )

    duration_ms = (time.monotonic() - start) * 1000
    browser_extraction = try_extract(url, rendered_html, used_browser=True)

    # Prefer quality over raw body length (matches crawl-engine escalation).
    http_doc_score = _preflight_quality_proxy(http_only_extraction)
    browser_doc_score = _preflight_quality_proxy(browser_extraction)
    if http_doc_score is not None and browser_doc_score is not None:
        improved = browser_doc_score > http_doc_score
    else:
        improved = browser_extraction.body_chars > http_only_extraction.body_chars

    return (
        BrowserFallbackResult(
            attempted=True,
            succeeded=True,
            rendered_content_chars=len(rendered_html),
            extraction_improved=improved,
            render_duration_ms=duration_ms,
        ),
        browser_extraction,
    )


def _preflight_quality_proxy(attempt: ExtractionAttempt) -> float | None:
    """Lightweight 0-1 score from preflight ExtractionAttempt fields."""
    if not attempt.extracted or attempt.body_chars <= 0:
        return 0.0 if attempt.fetched else None
    score = 0.0
    if attempt.title_found:
        score += 0.25
    if attempt.author_found:
        score += 0.15
    if attempt.date_found:
        score += 0.15
    # Body contribution saturates around 2k chars of useful text.
    score += min(0.45, attempt.body_chars / 2000 * 0.45)
    return round(score, 3)
