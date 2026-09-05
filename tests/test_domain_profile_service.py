from dataclasses import dataclass, field

from app.services.crawling.domain_profile_service import compute_health_score, decide_routing
from app.services.crawling.fetch_router import FetchStrategy


@dataclass
class FakeStats:
    http_attempts: int = 0
    http_successes: int = 0
    http_extraction_failures: int = 0
    browser_attempts: int = 0
    browser_successes: int = 0
    avg_http_latency_ms: float = 0.0
    avg_browser_latency_ms: float = 0.0
    failure_counts: dict = field(default_factory=dict)
    preferred_extractor: str | None = None


# -- health score --


def test_insufficient_observations_returns_neutral_score():
    stats = FakeStats(http_attempts=2, http_successes=2)
    result = compute_health_score(stats, min_observations=5)
    assert result.insufficient_data is True
    assert result.score == 50.0


def test_healthy_domain_scores_high():
    stats = FakeStats(
        http_attempts=20, http_successes=19, http_extraction_failures=1, avg_http_latency_ms=300,
    )
    result = compute_health_score(stats, min_observations=5)
    assert result.score > 85
    assert result.insufficient_data is False


def test_heavily_blocked_domain_scores_low_on_block_signal():
    stats = FakeStats(
        http_attempts=20, http_successes=15, avg_http_latency_ms=300,
        failure_counts={"http_403": 10, "http_429": 5},
    )
    result = compute_health_score(stats, min_observations=5)
    assert result.signals["block_rate"] > 0.5


def test_high_latency_reduces_score():
    fast = compute_health_score(
        FakeStats(http_attempts=10, http_successes=10, avg_http_latency_ms=200), min_observations=5
    )
    slow = compute_health_score(
        FakeStats(http_attempts=10, http_successes=10, avg_http_latency_ms=9000), min_observations=5
    )
    assert slow.score < fast.score


# -- routing decisions --


def test_explicit_strategy_always_wins_over_history():
    stats = FakeStats(http_attempts=50, http_successes=1, http_extraction_failures=45)
    decision = decide_routing(requested=FetchStrategy.HTTP, domain_stats=stats, min_observations=5)
    assert decision.strategy == FetchStrategy.HTTP
    assert "explicit" in decision.reasons[0]


def test_no_history_and_no_js_signal_defaults_to_http():
    decision = decide_routing(requested=FetchStrategy.AUTO, min_observations=5)
    assert decision.strategy == FetchStrategy.HTTP


def test_js_heuristic_used_when_no_history():
    decision = decide_routing(requested=FetchStrategy.AUTO, js_requires_browser=True, min_observations=5)
    assert decision.strategy == FetchStrategy.BROWSER


def test_insufficient_domain_history_does_not_influence_routing():
    # Only 2 attempts, both failed -- must NOT escalate to browser off this alone.
    stats = FakeStats(http_attempts=2, http_successes=0, http_extraction_failures=0)
    decision = decide_routing(requested=FetchStrategy.AUTO, domain_stats=stats, min_observations=5)
    assert decision.strategy == FetchStrategy.HTTP
    assert "no history" in decision.reasons[0]


def test_poor_http_extraction_and_good_browser_escalates():
    stats = FakeStats(
        http_attempts=20, http_successes=19, http_extraction_failures=17,
        browser_attempts=10, browser_successes=9,
    )
    decision = decide_routing(requested=FetchStrategy.AUTO, domain_stats=stats, min_observations=5)
    assert decision.strategy == FetchStrategy.BROWSER
    assert any("extraction failure" in r for r in decision.reasons)


def test_pattern_stats_take_precedence_over_domain_stats():
    # Domain overall looks fine, but THIS pattern is browser-only.
    domain_stats = FakeStats(http_attempts=100, http_successes=98)
    pattern_stats = FakeStats(
        http_attempts=20, http_successes=19, http_extraction_failures=18,
        browser_attempts=10, browser_successes=10,
    )
    decision = decide_routing(
        requested=FetchStrategy.AUTO, domain_stats=domain_stats, pattern_stats=pattern_stats, min_observations=5,
    )
    assert decision.strategy == FetchStrategy.BROWSER
    assert "URL pattern" in decision.reasons[0]


def test_browser_not_recommended_if_it_also_fails():
    stats = FakeStats(
        http_attempts=20, http_successes=5, http_extraction_failures=4,
        browser_attempts=15, browser_successes=1,
    )
    decision = decide_routing(requested=FetchStrategy.AUTO, domain_stats=stats, min_observations=5)
    assert decision.strategy == FetchStrategy.HTTP


def test_decision_carries_preferred_extractor_when_known():
    stats = FakeStats(http_attempts=20, http_successes=19, preferred_extractor="news")
    decision = decide_routing(requested=FetchStrategy.AUTO, domain_stats=stats, min_observations=5)
    assert decision.extractor == "news"


# -- basis (spec Phase 9 section 3: every learned decision exposes which
# precedence rung actually decided it, not just human-readable prose) --


def test_basis_is_explicit_when_strategy_requested():
    decision = decide_routing(requested=FetchStrategy.HTTP, min_observations=5)
    assert decision.basis == "explicit"


def test_basis_is_default_with_no_history_and_no_js_signal():
    decision = decide_routing(requested=FetchStrategy.AUTO, min_observations=5)
    assert decision.basis == "default"


def test_basis_is_js_heuristic_when_only_js_signal_present():
    decision = decide_routing(requested=FetchStrategy.AUTO, js_requires_browser=True, min_observations=5)
    assert decision.basis == "js_heuristic"


def test_basis_is_domain_when_only_domain_history_decides():
    stats = FakeStats(http_attempts=20, http_successes=19)
    decision = decide_routing(requested=FetchStrategy.AUTO, domain_stats=stats, min_observations=5)
    assert decision.basis == "domain"


def test_basis_is_url_pattern_when_pattern_history_decides_even_with_domain_history_present():
    domain_stats = FakeStats(http_attempts=100, http_successes=98)
    pattern_stats = FakeStats(http_attempts=20, http_successes=19)
    decision = decide_routing(
        requested=FetchStrategy.AUTO, domain_stats=domain_stats, pattern_stats=pattern_stats, min_observations=5,
    )
    assert decision.basis == "url_pattern"


def test_basis_is_default_when_history_exists_but_below_min_observations():
    # 2 attempts on both pattern and domain, threshold is 5 -- neither
    # should be allowed to decide, so this must fall all the way through.
    domain_stats = FakeStats(http_attempts=2, http_successes=0)
    pattern_stats = FakeStats(http_attempts=2, http_successes=0)
    decision = decide_routing(
        requested=FetchStrategy.AUTO, domain_stats=domain_stats, pattern_stats=pattern_stats, min_observations=5,
    )
    assert decision.basis == "default"
