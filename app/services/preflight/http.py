from __future__ import annotations

import time

import httpx

from app.core.config import Settings
from app.services.preflight.models import HTTPCheckResult, RedirectHop, TLSCheckResult
from app.services.security.url_security import URLSecurityError, URLSecurityService

MAX_REDIRECTS = 5


async def check_http(
    url: str, settings: Settings, security: URLSecurityService
) -> tuple[HTTPCheckResult, TLSCheckResult, httpx.Response | None]:
    """Fetch `url`, manually walking redirects so every hop is re-validated
    against the SSRF policy before being followed (the transport also pins
    each individual request, this loop additionally records the chain)."""
    start = time.monotonic()
    current_url = url
    chain: list[RedirectHop] = []
    used_https_anywhere = current_url.lower().startswith("https://")

    async with security.build_client(timeout=settings.preflight_http_timeout_seconds) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                security.validate_scheme(current_url)
            except URLSecurityError as exc:
                return (
                    HTTPCheckResult(reachable=False, error=str(exc)),
                    TLSCheckResult(used_https=used_https_anywhere),
                    None,
                )

            try:
                response = await client.get(current_url)
            except httpx.HTTPError as exc:
                duration_ms = (time.monotonic() - start) * 1000
                return (
                    HTTPCheckResult(
                        reachable=False,
                        response_time_ms=duration_ms,
                        error=f"{type(exc).__name__}: {exc}",
                    ),
                    TLSCheckResult(used_https=used_https_anywhere, valid_certificate=None),
                    None,
                )

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    break
                next_url = str(httpx.URL(current_url).join(location))
                chain.append(RedirectHop(url=current_url, status_code=response.status_code))
                current_url = next_url
                if current_url.lower().startswith("https://"):
                    used_https_anywhere = True
                continue

            duration_ms = (time.monotonic() - start) * 1000
            content_length_header = response.headers.get("content-length")
            result = HTTPCheckResult(
                reachable=True,
                status_code=response.status_code,
                final_url=str(response.url),
                redirect_chain=chain,
                response_time_ms=duration_ms,
                content_type=response.headers.get("content-type"),
                content_length=int(content_length_header) if content_length_header else len(response.content),
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
            tls = TLSCheckResult(used_https=used_https_anywhere, valid_certificate=True if used_https_anywhere else None)
            return result, tls, response

    duration_ms = (time.monotonic() - start) * 1000
    return (
        HTTPCheckResult(reachable=False, response_time_ms=duration_ms, error="too many redirects"),
        TLSCheckResult(used_https=used_https_anywhere),
        None,
    )
