import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from tests.conftest import make_settings
from uptime_monitor.checker import CheckResult
from uptime_monitor.monitor import Monitor


def result(target, minutes_ago, ok=True):
    return CheckResult(
        target,
        datetime.now(UTC) - timedelta(minutes=minutes_ago),
        ok,
        200 if ok else 503,
        50.0,
        None if ok else "unexpected status 503",
    )


def test_recent_is_newest_first_and_per_target(storage):
    storage.add([result("a", 3), result("a", 1), result("b", 2)])
    assert [r["checked_at"] for r in storage.recent("a")] == sorted(
        [r["checked_at"] for r in storage.recent("a")], reverse=True
    )
    assert len(storage.recent("a")) == 2 and len(storage.recent("a", limit=1)) == 1


def test_since_and_prune(storage):
    storage.add([result("a", 60 * 48), result("a", 30, ok=False), result("a", 5)])
    rows = storage.since("a", datetime.now(UTC) - timedelta(hours=1))
    assert sorted(r["ok"] for r in rows) == [0, 1]
    assert storage.prune(datetime.now(UTC) - timedelta(days=1)) == 1
    assert len(storage.recent("a")) == 2


def test_run_once_checks_every_target_and_stores_results(storage):
    settings = make_settings(
        {"name": "up", "url": "https://up.test/"}, {"name": "down", "url": "https://down.test/"}
    )

    def handler(request):
        return httpx.Response(200 if request.url.host == "up.test" else 500)

    monitor = Monitor(settings, storage, httpx.MockTransport(handler))
    results = asyncio.run(monitor.run_once())
    assert {r.target: r.ok for r in results} == {"up": True, "down": False}
    assert storage.recent("down")[0]["status_code"] == 500
    assert monitor.last_run is not None


def test_run_once_prunes_beyond_retention(storage):
    settings = make_settings()
    storage.add([result("site", 60 * 24 * 45)])  # 45 days old, default retention 30
    asyncio.run(
        Monitor(settings, storage, httpx.MockTransport(lambda r: httpx.Response(200))).run_once()
    )
    assert len(storage.recent("site")) == 1


def test_run_forever_survives_a_failing_cycle(storage, monkeypatch):
    settings = make_settings()
    monitor = Monitor(settings, storage, httpx.MockTransport(lambda r: httpx.Response(200)))
    calls = {"n": 0}
    real_run_once = monitor.run_once

    async def flaky_run_once():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk full")
        return await real_run_once()

    async def fake_sleep(_):
        if calls["n"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(monitor, "run_once", flaky_run_once)
    monkeypatch.setattr("uptime_monitor.monitor.asyncio.sleep", fake_sleep)
    try:
        asyncio.run(monitor.run_forever())
    except asyncio.CancelledError:
        pass
    assert calls["n"] == 2 and len(storage.recent("site")) == 1
