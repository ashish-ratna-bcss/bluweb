from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from playwright.async_api import Route, async_playwright

from app.core.config import Settings
from app.services.crawling.browser_scroll import ScrollBudget, ScrollResult, expand_dynamic_content
from app.services.security.url_security import URLSecurityError, URLSecurityService

MAX_REDIRECTS = 5


@dataclass
class PageFetchResult:
    success: bool
    final_url: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    html: str | None = None
    raw_bytes: bytes | None = None
    latency_ms: float = 0.0
    used_browser: bool = False
    error: str | None = None
    scroll: ScrollResult | None = None


async def http_fetch_page(
    url: str, settings: Settings, security: URLSecurityService, *, max_bytes: int
) -> PageFetchResult:
    start = time.monotonic()
    current_url = url

    async with security.build_client(timeout=settings.preflight_http_timeout_seconds) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                security.validate_scheme(current_url)
            except URLSecurityError as exc:
                return PageFetchResult(success=False, error=str(exc), latency_ms=(time.monotonic() - start) * 1000)

            try:
                response = await client.get(current_url)
            except httpx.HTTPError as exc:
                return PageFetchResult(
                    success=False,
                    error=f"{type(exc).__name__}: {exc}",
                    latency_ms=(time.monotonic() - start) * 1000,
                )

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    break
                current_url = str(httpx.URL(current_url).join(location))
                continue

            if len(response.content) > max_bytes:
                return PageFetchResult(
                    success=False,
                    error=f"response exceeded max_response_bytes ({max_bytes})",
                    latency_ms=(time.monotonic() - start) * 1000,
                )

            content_type = response.headers.get("content-type", "")
            is_html = "html" in content_type
            return PageFetchResult(
                success=True,
                final_url=str(response.url),
                status_code=response.status_code,
                content_type=content_type,
                html=response.text if is_html else None,
                raw_bytes=response.content,
                latency_ms=(time.monotonic() - start) * 1000,
            )

    return PageFetchResult(success=False, error="too many redirects", latency_ms=(time.monotonic() - start) * 1000)


async def _guarded_route(route: Route, security: URLSecurityService) -> None:
    try:
        hostname = security.validate_scheme(route.request.url)
        await security.resolve_and_validate(hostname)
    except URLSecurityError:
        await route.abort()
        return
    await route.continue_()


async def browser_fetch_page(
    url: str,
    settings: Settings,
    security: URLSecurityService,
    *,
    enable_scroll: bool = False,
    scroll_budget: ScrollBudget | None = None,
) -> PageFetchResult:
    """Fetch via Playwright. Optional bounded scroll/load-more expansion for
    index/infinite-scroll pages. Always timeout-controlled and route-guarded.
    """
    start = time.monotonic()
    timeout_ms = int(settings.preflight_browser_timeout_seconds * 1000)
    budget = scroll_budget or ScrollBudget(
        max_scrolls=getattr(settings, "crawl_max_scrolls", 5),
        max_new_items=getattr(settings, "crawl_max_scroll_new_items", 200),
        max_browser_seconds=min(
            getattr(settings, "crawl_max_browser_seconds_per_page", 20.0),
            settings.preflight_browser_timeout_seconds,
        ),
        max_total_bytes=getattr(settings, "crawl_max_response_bytes", 5_000_000),
        max_consecutive_no_change=2,
    )

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(user_agent=settings.crawler_user_agent)
                page = await context.new_page()
                await page.route("**/*", lambda route: _guarded_route(route, security))
                response = await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                # Bounded settle for delayed/lazy content without requiring
                # full networkidle (which hangs on long-polling SPAs).
                try:
                    await page.wait_for_load_state("networkidle", timeout=min(5000, timeout_ms))
                except Exception:  # noqa: BLE001
                    await page.wait_for_timeout(500)

                scroll_result = None
                if enable_scroll:
                    scroll_result = await expand_dynamic_content(page, budget, started_monotonic=start)
                    html = scroll_result.html or await page.content()
                else:
                    # Still inline open shadow roots once for rendered extraction.
                    try:
                        await page.evaluate(
                            """() => {
                              for (const el of document.querySelectorAll('*')) {
                                if (el.shadowRoot) {
                                  const slot = document.createElement('div');
                                  slot.setAttribute('data-webintel-shadow', '1');
                                  slot.innerHTML = el.shadowRoot.innerHTML;
                                  el.appendChild(slot);
                                }
                              }
                            }"""
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    html = await page.content()
                    scroll_result = ScrollResult(stopped_reason="scroll_disabled", html=html)

                final_url = page.url
                status_code = response.status if response else None
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 - render failures are a fetch outcome, not a crash
        return PageFetchResult(
            success=False,
            used_browser=True,
            error=str(exc),
            latency_ms=(time.monotonic() - start) * 1000,
        )

    return PageFetchResult(
        success=True,
        final_url=final_url,
        status_code=status_code,
        content_type="text/html",
        html=html,
        raw_bytes=html.encode("utf-8"),
        used_browser=True,
        latency_ms=(time.monotonic() - start) * 1000,
        scroll=scroll_result,
    )
