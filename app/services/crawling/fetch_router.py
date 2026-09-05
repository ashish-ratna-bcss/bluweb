"""Adaptive HTTP-vs-browser routing.

ponytail: deterministic scoring against persisted per-domain stats, not a
learned model -- this is exactly what section 24 of the spec asks for
("do NOT build an ML model initially... design the interface so a learned
model can be added later"). `decide_initial_strategy` is that interface
seam: swap its body for a model call later without touching call sites.

Deliberately not using Crawlee's built-in AdaptivePlaywrightCrawler: it
would mean funneling every fetch through Crawlee's own HTTP client, which
can't easily carry our SSRF-pinning transport. We still get real adaptivity
-- domain-level stats bias the first attempt, and a fetched page's
JS-dependency heuristic (reused from the pre-flight engine) can escalate a
single page to the browser regardless of domain history.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.db.models.crawl import FetchStrategyStats

_MIN_ATTEMPTS_FOR_BIAS = 5
_HTTP_FAILURE_RATE_THRESHOLD = 0.6


class FetchStrategy(StrEnum):
    HTTP = "http"
    BROWSER = "browser"
    AUTO = "auto"


@dataclass(frozen=True)
class FetchOutcome:
    strategy: FetchStrategy
    success: bool
    extraction_ok: bool
    latency_ms: float


def decide_initial_strategy(requested: FetchStrategy, stats: FetchStrategyStats | None) -> FetchStrategy:
    """Pick the strategy to try first for a page on this domain."""
    if requested != FetchStrategy.AUTO:
        return requested

    if stats is None or stats.http_attempts < _MIN_ATTEMPTS_FOR_BIAS:
        return FetchStrategy.HTTP

    http_failure_rate = 1 - (stats.http_successes / stats.http_attempts) if stats.http_attempts else 0
    extraction_failure_rate = (
        stats.http_extraction_failures / stats.http_successes if stats.http_successes else 0
    )
    browser_looks_viable = stats.browser_attempts == 0 or (
        stats.browser_successes / stats.browser_attempts > 0.5
    )

    if (
        http_failure_rate > _HTTP_FAILURE_RATE_THRESHOLD or extraction_failure_rate > _HTTP_FAILURE_RATE_THRESHOLD
    ) and browser_looks_viable:
        return FetchStrategy.BROWSER

    return FetchStrategy.HTTP
