"""Domain intelligence: turns the counters `FetchStrategyStats`/
`URLPatternStats` already accumulate into (a) an explainable health score
and (b) a routing decision, per spec Phase 6 sections 4-10.

Deliberately pure functions taking already-loaded stats objects, no DB
access here -- repositories fetch, this module decides. That's what makes
the decision logic unit-testable without a database (see
tests/test_domain_profile_service.py) and keeps the "no ML yet, but a
model-ready seam" property FetchStrategyRouter already had: swap
`decide_routing`'s body for a model call later without touching callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.crawling.fetch_router import FetchStrategy
from app.services.crawling.failure_classification import BLOCKED_OR_THROTTLED

# Health score weights (spec section 5: "choose and document reasonable
# weights"). Sum to 100. Availability weighted highest because an
# unreachable domain makes every other signal moot; latency weighted
# lowest because it degrades UX, not correctness.
_WEIGHT_AVAILABILITY = 40
_WEIGHT_EXTRACTION = 30
_WEIGHT_BLOCK = 15
_WEIGHT_LATENCY = 15

_LATENCY_FLOOR_MS = 1000.0  # at/under this, full latency score
_LATENCY_CEILING_MS = 10000.0  # at/over this, zero latency score


@dataclass
class HealthScore:
    score: float  # 0-100
    signals: dict[str, float] = field(default_factory=dict)
    insufficient_data: bool = False


@dataclass
class RoutingDecision:
    strategy: FetchStrategy
    reasons: list[str] = field(default_factory=list)
    extractor: str | None = None
    # Which precedence rung actually decided this (spec Phase 9 section 3:
    # "every learned decision should expose a reason") -- explicit string
    # rather than re-parsing `reasons` text, which exists for humans, not
    # for metrics/logic to pattern-match against.
    basis: str = "default"  # "explicit" | "url_pattern" | "domain" | "js_heuristic" | "default"


def compute_health_score(stats, *, min_observations: int) -> HealthScore:
    """`stats` is a FetchStrategyStats row (or anything with the same
    attribute names) -- kept duck-typed rather than importing the ORM
    model here so this stays a pure function with no DB-layer dependency.
    """
    total_attempts = stats.http_attempts + stats.browser_attempts
    if total_attempts < min_observations:
        return HealthScore(score=50.0, signals={"total_observations": total_attempts}, insufficient_data=True)

    total_successes = stats.http_successes + stats.browser_successes
    availability_rate = total_successes / total_attempts if total_attempts else 0.0

    extraction_attempts = stats.http_successes  # extraction only runs on a successful HTTP fetch today
    extraction_failures = stats.http_extraction_failures
    extraction_rate = (
        (extraction_attempts - extraction_failures) / extraction_attempts if extraction_attempts else 1.0
    )

    blocked_count = sum(
        count for category, count in stats.failure_counts.items()
        if category in {c.value for c in BLOCKED_OR_THROTTLED}
    )
    block_rate = blocked_count / total_attempts if total_attempts else 0.0

    avg_latency = stats.avg_http_latency_ms or stats.avg_browser_latency_ms or 0.0
    latency_rate = _latency_to_rate(avg_latency)

    availability_score = availability_rate * _WEIGHT_AVAILABILITY
    extraction_score = extraction_rate * _WEIGHT_EXTRACTION
    block_score = (1 - block_rate) * _WEIGHT_BLOCK
    latency_score = latency_rate * _WEIGHT_LATENCY

    total = round(availability_score + extraction_score + block_score + latency_score, 1)

    return HealthScore(
        score=total,
        signals={
            "http_success_rate": round(availability_rate, 4),
            "extraction_success_rate": round(extraction_rate, 4),
            "block_rate": round(block_rate, 4),
            "avg_latency_ms": round(avg_latency, 1),
        },
    )


def _latency_to_rate(latency_ms: float) -> float:
    if latency_ms <= _LATENCY_FLOOR_MS:
        return 1.0
    if latency_ms >= _LATENCY_CEILING_MS:
        return 0.0
    span = _LATENCY_CEILING_MS - _LATENCY_FLOOR_MS
    return 1.0 - (latency_ms - _LATENCY_FLOOR_MS) / span


def decide_routing(
    *,
    requested: FetchStrategy,
    domain_stats=None,
    pattern_stats=None,
    js_requires_browser: bool = False,
    min_observations: int = 5,
) -> RoutingDecision:
    """Precedence (spec section 9), each step returning as soon as it has
    an answer:
      1. explicit user configuration (requested != AUTO)
      2. URL-pattern profile (most specific -- this exact kind of page on
         this exact domain)
      3. domain profile (coarser, but still real history)
      4. JS heuristic (this specific page looks client-rendered)
      5. HTTP default
    """
    if requested != FetchStrategy.AUTO:
        return RoutingDecision(strategy=requested, reasons=["explicit strategy requested"], basis="explicit")

    if pattern_stats is not None:
        decision = _decide_from_stats(pattern_stats, min_observations, scope="URL pattern", basis="url_pattern")
        if decision is not None:
            return decision

    if domain_stats is not None:
        decision = _decide_from_stats(domain_stats, min_observations, scope="domain", basis="domain")
        if decision is not None:
            return decision

    if js_requires_browser:
        return RoutingDecision(
            strategy=FetchStrategy.BROWSER, reasons=["JS-dependency heuristic flagged this page"], basis="js_heuristic"
        )

    return RoutingDecision(strategy=FetchStrategy.HTTP, reasons=["no history, no JS signal -- HTTP default"], basis="default")


def _decide_from_stats(stats, min_observations: int, *, scope: str, basis: str) -> RoutingDecision | None:
    http_attempts = getattr(stats, "http_attempts", 0)
    browser_attempts = getattr(stats, "browser_attempts", 0)
    if http_attempts + browser_attempts < min_observations:
        return None

    http_successes = getattr(stats, "http_successes", 0)
    http_extraction_failures = getattr(stats, "http_extraction_failures", 0)
    browser_successes = getattr(stats, "browser_successes", 0)

    http_failure_rate = 1 - (http_successes / http_attempts) if http_attempts else 0.0
    extraction_failure_rate = (
        http_extraction_failures / http_successes if http_successes else 0.0
    )
    browser_viable = browser_attempts == 0 or (browser_successes / browser_attempts > 0.5)

    preferred_extractor = getattr(stats, "preferred_extractor", None)

    # Quality-compare feedback: when historical browser renders win more often
    # than HTTP on this domain/pattern, prefer browser even if HTTP "succeeds".
    browser_superior = getattr(stats, "browser_superior_count", 0) or 0
    http_superior = getattr(stats, "http_superior_count", 0) or 0
    compare_total = browser_superior + http_superior + (getattr(stats, "browser_equivalent_count", 0) or 0)
    if compare_total >= min_observations and browser_viable:
        browser_win_rate = browser_superior / compare_total
        if browser_win_rate >= 0.6:
            return RoutingDecision(
                strategy=FetchStrategy.BROWSER,
                reasons=[
                    f"{scope} browser superior in {browser_superior}/{compare_total} quality compares "
                    f"({browser_win_rate:.0%})"
                ],
                extractor=preferred_extractor,
                basis=basis,
            )

    avg_quality = getattr(stats, "avg_extraction_quality", 0.0) or 0.0
    quality_obs = getattr(stats, "quality_observations", 0) or 0
    if (
        quality_obs >= min_observations
        and avg_quality < 0.4
        and browser_viable
        and http_attempts >= min_observations
    ):
        return RoutingDecision(
            strategy=FetchStrategy.BROWSER,
            reasons=[
                f"{scope} avg HTTP extraction quality {avg_quality:.2f} below threshold "
                f"across {quality_obs} observations"
            ],
            extractor=preferred_extractor,
            basis=basis,
        )

    if (http_failure_rate > 0.6 or extraction_failure_rate > 0.6) and browser_viable:
        reasons = [f"{scope} profile has {http_attempts} HTTP observations"]
        if extraction_failure_rate > 0.6:
            reasons.append(f"HTTP extraction failure rate {extraction_failure_rate:.0%}")
        if http_failure_rate > 0.6:
            reasons.append(f"HTTP failure rate {http_failure_rate:.0%}")
        reasons.append(f"browser success rate {(browser_successes / browser_attempts if browser_attempts else 1):.0%}")
        return RoutingDecision(strategy=FetchStrategy.BROWSER, reasons=reasons, extractor=preferred_extractor, basis=basis)

    return RoutingDecision(
        strategy=FetchStrategy.HTTP,
        reasons=[f"{scope} profile: HTTP success rate {(1 - http_failure_rate):.0%}, sufficient observations"],
        extractor=preferred_extractor,
        basis=basis,
    )
