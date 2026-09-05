from app.db.repositories.source_repository import compute_next_interval


def test_change_snaps_back_to_minimum():
    assert compute_next_interval(
        current_interval_seconds=7200, min_interval_seconds=900, max_interval_seconds=86400, had_changes=True
    ) == 900


def test_unchanged_doubles_the_interval():
    assert compute_next_interval(
        current_interval_seconds=900, min_interval_seconds=900, max_interval_seconds=86400, had_changes=False
    ) == 1800


def test_unchanged_repeatedly_approaches_but_never_exceeds_maximum():
    interval = 900
    for _ in range(20):
        interval = compute_next_interval(
            current_interval_seconds=interval, min_interval_seconds=900, max_interval_seconds=86400,
            had_changes=False,
        )
        assert interval <= 86400
    assert interval == 86400


def test_minimum_equal_to_maximum_stays_constant():
    assert compute_next_interval(
        current_interval_seconds=900, min_interval_seconds=900, max_interval_seconds=900, had_changes=False
    ) == 900
