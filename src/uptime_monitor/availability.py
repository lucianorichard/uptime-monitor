"""How availability is calculated. Kept separate and pure so the definition
is explicit, documented and tested.

Definition
----------
A *check* is one GET request. It is **successful** when a response is received
within the timeout and its status code is in the target's expected set
(default 200-399). Every other outcome (timeout, DNS/TCP/TLS error, unexpected
status) is a **failed** check.

    availability(window) = successful checks / total checks in the window x 100

Every check has the same weight. Windows with no checks have *no data*, not
100%. Because availability is sampled, an outage shorter than the interval can
go unnoticed, and periods when the monitor itself was not running are simply
missing. *Coverage* makes those gaps visible:

    coverage(window) = checks performed / checks expected (window / interval) x 100
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowStats:
    total: int
    successful: int
    availability: float | None  # percent, None when there is no data
    coverage: float | None  # percent of expected checks that actually ran
    p50_ms: float | None
    p95_ms: float | None


def availability(successful: int, total: int) -> float | None:
    if total < 0 or not 0 <= successful <= total:
        raise ValueError("need 0 <= successful <= total")
    return None if total == 0 else 100.0 * successful / total


def coverage(total: int, window_seconds: float, interval_seconds: float) -> float | None:
    expected = math.floor(window_seconds / interval_seconds)
    if expected <= 0:
        return None
    return min(100.0, 100.0 * total / expected)


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile (no interpolation): an actually observed value."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def window_stats(
    oks: list[bool], latencies_ms: list[float], window_seconds: float, interval_seconds: float
) -> WindowStats:
    total, successful = len(oks), sum(oks)
    return WindowStats(
        total=total,
        successful=successful,
        availability=availability(successful, total),
        coverage=coverage(total, window_seconds, interval_seconds),
        p50_ms=percentile(latencies_ms, 50),
        p95_ms=percentile(latencies_ms, 95),
    )
