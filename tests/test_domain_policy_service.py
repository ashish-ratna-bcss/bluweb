from datetime import datetime, timedelta, timezone

from app.services.crawling.domain_policy_service import (
    OPEN_DURATION_SECONDS,
    OPEN_THRESHOLD,
    DEGRADED_THRESHOLD,
    PolicyState,
    should_allow_request,
    update_policy_after_outcome,
)
from app.services.crawling.failure_classification import FailureCategory

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _healthy_state() -> PolicyState:
    return PolicyState(crawl_delay_ms=0.0, recommended_concurrency=5, circuit_state="healthy", circuit_opened_at=None, consecutive_failures=0)


def test_single_failure_does_not_open_circuit():
    state = update_policy_after_outcome(_healthy_state(), failure_category=FailureCategory.HTTP_429, now=NOW)
    assert state.circuit_state != "open"
    assert state.crawl_delay_ms > 0  # did slow down


def test_single_429_is_not_permanent_throttle():
    degraded = update_policy_after_outcome(_healthy_state(), failure_category=FailureCategory.HTTP_429, now=NOW)
    recovered = update_policy_after_outcome(degraded, failure_category=FailureCategory.NONE, now=NOW)
    assert recovered.crawl_delay_ms < degraded.crawl_delay_ms
    assert recovered.consecutive_failures == 0


def test_repeated_429_increases_delay_and_eventually_opens_circuit():
    state = _healthy_state()
    for _ in range(OPEN_THRESHOLD):
        state = update_policy_after_outcome(state, failure_category=FailureCategory.HTTP_429, now=NOW)
    assert state.circuit_state == "open"
    assert state.circuit_opened_at == NOW


def test_degraded_before_open():
    state = _healthy_state()
    for _ in range(DEGRADED_THRESHOLD):
        state = update_policy_after_outcome(state, failure_category=FailureCategory.HTTP_429, now=NOW)
    assert state.circuit_state == "degraded"
    assert state.recommended_concurrency == 1


def test_delay_never_exceeds_cap_even_with_many_failures():
    state = _healthy_state()
    for _ in range(50):
        state = update_policy_after_outcome(state, failure_category=FailureCategory.HTTP_429, now=NOW)
    assert state.crawl_delay_ms <= 30_000.0


def test_open_circuit_blocks_requests_until_backoff_elapses():
    state = PolicyState(0, 1, "open", NOW, OPEN_THRESHOLD)
    assert should_allow_request(state, now=NOW + timedelta(seconds=1)) is False
    assert should_allow_request(state, now=NOW + timedelta(seconds=OPEN_DURATION_SECONDS)) is True


def test_healthy_circuit_always_allows_requests():
    assert should_allow_request(_healthy_state(), now=NOW) is True


def test_successful_probe_after_open_closes_circuit():
    open_state = PolicyState(5000, 1, "open", NOW, OPEN_THRESHOLD)
    recovered = update_policy_after_outcome(open_state, failure_category=FailureCategory.NONE, now=NOW)
    assert recovered.circuit_state == "healthy"
    assert recovered.recommended_concurrency == 5  # default max, snapped back


def test_failed_probe_after_open_reopens_with_fresh_timer():
    open_state = PolicyState(5000, 1, "open", NOW, OPEN_THRESHOLD)
    later = NOW + timedelta(seconds=OPEN_DURATION_SECONDS)
    still_failing = update_policy_after_outcome(open_state, failure_category=FailureCategory.HTTP_429, now=later)
    assert still_failing.circuit_state == "open"
    assert still_failing.circuit_opened_at == NOW  # timer doesn't reset while already open+failing more


# -- server-declared Retry-After (Universal Adaptive Web Intelligence
# next-phase item 9: wire the value that was already being parsed but never
# consumed downstream) --


def test_server_retry_after_extends_delay_beyond_computed_backoff():
    # Fixed multiplier alone would give 500ms (the throttle floor from a
    # cold state); a server asking for 10s should win.
    state = update_policy_after_outcome(
        _healthy_state(), failure_category=FailureCategory.HTTP_429, now=NOW, server_retry_after_seconds=10.0,
    )
    assert state.crawl_delay_ms == 10_000.0


def test_server_retry_after_never_shortens_computed_backoff():
    # A domain already backed off to 20s that returns Retry-After: 1 must
    # not have its delay shortened just because the server said "1".
    degraded_state = PolicyState(crawl_delay_ms=20_000.0, recommended_concurrency=1, circuit_state="degraded", circuit_opened_at=None, consecutive_failures=3)
    state = update_policy_after_outcome(
        degraded_state, failure_category=FailureCategory.HTTP_429, now=NOW, server_retry_after_seconds=1.0,
    )
    assert state.crawl_delay_ms >= 20_000.0 * 1.5  # still applies its own multiplier, not overridden downward


def test_server_retry_after_still_capped_at_max_delay():
    state = update_policy_after_outcome(
        _healthy_state(), failure_category=FailureCategory.HTTP_429, now=NOW, server_retry_after_seconds=3600.0,
    )
    assert state.crawl_delay_ms == 30_000.0


def test_one_browser_failure_does_not_disable_browser_globally():
    # This module doesn't track browser separately from HTTP failures --
    # confirms a single failure of any kind stays in "healthy", i.e. no
    # blanket disabling from one bad outcome.
    state = update_policy_after_outcome(_healthy_state(), failure_category=FailureCategory.TIMEOUT, now=NOW)
    assert state.circuit_state == "healthy"
