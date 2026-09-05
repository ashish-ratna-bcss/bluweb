"""Domain-aware politeness + circuit breaker (spec Phase 6 sections 24-27).

Incremental state machine, not a batch recomputation: `update_policy_after_outcome`
is called once per fetch outcome and nudges delay/concurrency/circuit state
from wherever they currently are, the same way a real rate limiter reacts
to individual responses rather than recomputing from full history each
time. Circuit states:

    healthy -> degraded -> open -> (half-open probe) -> healthy | open

ponytail: recovery is delay decaying multiplicatively per success and
concurrency snapping back to the configured max only once the circuit
fully closes again (not a per-success +1 ramp) -- simpler than a token-
bucket, still satisfies "never instantly return to maximum concurrency"
since a closed circuit already required surviving a half-open probe.
Swap in a gradual per-success concurrency ramp if real traffic shows the
snap-back is too aggressive.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.services.crawling.failure_classification import BLOCKED_OR_THROTTLED, FailureCategory

DEGRADED_THRESHOLD = 3  # consecutive failures before slowing down
OPEN_THRESHOLD = 6  # consecutive failures before stopping entirely
OPEN_DURATION_SECONDS = 120  # how long the circuit stays open before a probe
MIN_DELAY_MS = 0.0
MAX_DELAY_MS = 30_000.0
DEFAULT_MAX_CONCURRENCY = 5


@dataclass
class PolicyState:
    crawl_delay_ms: float
    recommended_concurrency: int
    circuit_state: str  # healthy | degraded | open
    circuit_opened_at: datetime | None
    consecutive_failures: int


def update_policy_after_outcome(
    state: PolicyState, *, failure_category: FailureCategory, now: datetime, max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> PolicyState:
    success = failure_category == FailureCategory.NONE

    if success:
        # Any success -- including a half-open probe after the circuit was
        # open -- closes it. `should_allow_request` is the gate that
        # decided whether this attempt was even allowed to happen while
        # open, so a success reaching here has already earned full reset.
        consecutive_failures = 0
        new_delay = max(MIN_DELAY_MS, state.crawl_delay_ms * 0.9)
        if new_delay < 50:
            new_delay = MIN_DELAY_MS
        return PolicyState(new_delay, max_concurrency, "healthy", None, consecutive_failures)

    consecutive_failures = state.consecutive_failures + 1
    is_throttle_signal = failure_category in BLOCKED_OR_THROTTLED

    multiplier = 2.0 if is_throttle_signal else 1.5
    floor_ms = 500.0 if is_throttle_signal else 200.0
    new_delay = min(MAX_DELAY_MS, max(floor_ms, state.crawl_delay_ms * multiplier))

    if consecutive_failures >= OPEN_THRESHOLD:
        circuit_state = "open"
        circuit_opened_at = state.circuit_opened_at or now
        concurrency = 1
    elif consecutive_failures >= DEGRADED_THRESHOLD:
        circuit_state = "degraded"
        circuit_opened_at = None
        concurrency = 1
    else:
        circuit_state = state.circuit_state if state.circuit_state != "healthy" else "healthy"
        circuit_opened_at = None
        concurrency = state.recommended_concurrency

    return PolicyState(new_delay, concurrency, circuit_state, circuit_opened_at, consecutive_failures)


def should_allow_request(state: PolicyState, *, now: datetime) -> bool:
    """Circuit-breaker gate: called before a fetch is attempted. Open means
    stop entirely until the backoff window elapses, at which point exactly
    one probe request is allowed through (the caller's next outcome will
    call `update_policy_after_outcome` and either close the circuit again
    or re-open it with a fresh timer)."""
    if state.circuit_state != "open":
        return True
    if state.circuit_opened_at is None:
        return True
    return now >= state.circuit_opened_at + timedelta(seconds=OPEN_DURATION_SECONDS)
