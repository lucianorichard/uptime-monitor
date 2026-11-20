import pytest

from uptime_monitor.availability import availability, coverage, percentile, window_stats


def test_availability_is_successful_over_total():
    assert availability(99, 100) == 99.0
    assert availability(0, 4) == 0.0
    assert availability(3, 3) == 100.0


def test_no_checks_means_no_data_not_100_percent():
    assert availability(0, 0) is None


@pytest.mark.parametrize("successful, total", [(5, 4), (-1, 4), (0, -1)])
def test_invalid_counts(successful, total):
    with pytest.raises(ValueError):
        availability(successful, total)


def test_coverage_shows_monitoring_gaps():
    day = 24 * 3600
    assert coverage(1440, day, 60) == 100.0
    assert coverage(720, day, 60) == 50.0  # the monitor was down half of the day
    assert coverage(1500, day, 60) == 100.0  # capped (restarts can add extra checks)
    assert coverage(0, 30, 60) is None  # window shorter than one interval


def test_percentile_is_an_observed_value():
    values = [10, 20, 30, 40, 1000]
    assert percentile(values, 50) == 30
    assert percentile(values, 95) == 1000
    assert percentile([], 95) is None


def test_window_stats():
    s = window_stats([True, True, False, True], [100, 120, 300], 240, 60)
    assert (s.total, s.successful, s.availability, s.coverage) == (4, 3, 75.0, 100.0)
    assert (s.p50_ms, s.p95_ms) == (120, 300)
