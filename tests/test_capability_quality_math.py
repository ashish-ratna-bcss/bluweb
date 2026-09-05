"""Pure-function coverage for the running-average math backing the new
Phase 9 quality-tracking columns (avg_extraction_quality on both
FetchStrategyStats and URLPatternStats). Real DB-backed integration/
concurrency testing of `record_fetch_outcome`/`record_pattern_outcome`
needs Postgres (JSONB columns, `ON CONFLICT DO NOTHING`, `SELECT ... FOR
UPDATE` are all Postgres-only) and is explicitly NOT run in this session --
see the Phase 9 final report's Concurrency/Data Integrity section for why
and what already-proven pattern this reuses instead of re-deriving one.
"""

from app.db.repositories.crawl_repository import _running_average


def test_first_observation_becomes_the_average():
    assert _running_average(0.0, 1, 0.8) == 0.8


def test_average_converges_toward_repeated_observations():
    avg = 0.0
    for i in range(1, 11):
        avg = _running_average(avg, i, 0.9)
    assert round(avg, 6) == 0.9


def test_average_reflects_mixed_observations_correctly():
    # Three observations: 1.0, 0.0, 0.5 -- true mean is 0.5.
    avg = _running_average(0.0, 1, 1.0)
    avg = _running_average(avg, 2, 0.0)
    avg = _running_average(avg, 3, 0.5)
    assert round(avg, 6) == 0.5


def test_a_single_bad_observation_does_not_dominate_a_long_history():
    avg = 0.0
    for i in range(1, 51):
        avg = _running_average(avg, i, 0.95)
    avg = _running_average(avg, 51, 0.0)
    assert avg > 0.9  # one bad quality score among 50 good ones barely moves the average
