from __future__ import annotations

from urllib.parse import urljoin

from app.core.config import Settings
from app.services.preflight.models import RobotsCheckResult
from app.services.security.url_security import URLSecurityService


async def check_robots(
    final_url: str, settings: Settings, security: URLSecurityService
) -> RobotsCheckResult:
    robots_url = urljoin(final_url, "/robots.txt")
    try:
        async with security.build_client(timeout=settings.preflight_http_timeout_seconds) as client:
            response = await client.get(robots_url)
    except Exception as exc:  # noqa: BLE001 - robots.txt absence must never fail preflight
        return RobotsCheckResult(exists=False, error=str(exc))

    if response.status_code >= 400:
        return RobotsCheckResult(exists=False)

    text = response.text
    sitemap_urls = [
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.strip().lower().startswith("sitemap:")
    ]
    fetch_allowed = _allows_our_agent(text, settings.crawler_user_agent)

    return RobotsCheckResult(exists=True, fetch_allowed=fetch_allowed, sitemap_urls=sitemap_urls)


def _allows_our_agent(robots_text: str, user_agent: str) -> bool:
    """Minimal robots.txt rule check for the root path ("/") under our
    configured user agent, falling back to "*".

    ponytail: this is a coarse check (root-path Disallow only), not a full
    RFC 9309 matcher. Good enough to flag "site opts out entirely"; a real
    crawl still needs per-URL robots evaluation (Protego, pulled in
    transitively via crawlee) before fetching individual pages.
    """
    our_agent = user_agent.split("/")[0].strip().lower()
    groups: dict[str, list[str]] = {}
    current_agents: list[str] = []

    for raw_line in robots_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()

        if field == "user-agent":
            current_agents = [value.lower()]
        elif field == "disallow" and current_agents:
            for agent in current_agents:
                groups.setdefault(agent, []).append(value)

    for agent_key in (our_agent, "*"):
        if agent_key in groups:
            return "/" not in groups[agent_key]

    return True
