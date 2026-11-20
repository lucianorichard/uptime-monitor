import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.conftest import make_settings
from uptime_monitor.app import create_app
from uptime_monitor.checker import CheckResult


def handler(request):
    return httpx.Response({"up.test": 200, "down.test": 503}.get(request.url.host, 200))


@pytest.fixture
def setup(storage):
    settings = make_settings(
        {"name": "up", "url": "https://up.test/"},
        {"name": "down", "url": "https://down.test/"},
        {"name": "new", "url": "https://new.test/"},
    )
    app = create_app(settings, storage, run_monitor=False, transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        # one real (simulated) cycle for "up" and "down"; "new" has no data yet
        monitor = app.state.monitor
        monitor.settings = make_settings(
            *[{"name": t.name, "url": t.url} for t in settings.targets[:2]]
        )
        asyncio.run(monitor.run_once())
        yield client, app, storage


def test_dashboard_shows_current_state(setup):
    client, _, _ = setup
    html = client.get("/").text
    for text in ("UP", "DOWN", "NO DATA", "unexpected status 503", "https://up.test/"):
        assert text in html


def test_api_status(setup):
    client, _, _ = setup
    targets = {t["name"]: t for t in client.get("/api/status").json()["targets"]}
    assert targets["up"]["state"] == "up"
    assert targets["down"]["state"] == "down"
    assert targets["new"]["state"] == "no-data"
    assert targets["up"]["windows"]["24h"]["availability"] == 100.0
    assert targets["new"]["windows"]["24h"]["availability"] is None


def test_stale_when_the_monitor_stopped_checking(setup):
    client, _, storage = setup
    old = datetime.now(UTC) - timedelta(hours=1)
    storage.add([CheckResult("new", old, True, 200, 10.0, None)])
    states = {t["name"]: t["state"] for t in client.get("/api/status").json()["targets"]}
    assert states["new"] == "stale"


def test_target_page_and_history(setup):
    client, _, _ = setup
    page = client.get("/targets/down")
    assert page.status_code == 200 and "FAIL" in page.text and "<svg" in page.text
    checks = client.get("/api/targets/down/checks", params={"limit": 5}).json()
    assert checks[0]["status_code"] == 503 and checks[0]["ok"] == 0


def test_unknown_targets_are_404(setup):
    client, _, _ = setup
    assert client.get("/targets/evil").status_code == 404
    assert client.get("/api/targets/evil/checks").status_code == 404


def test_limit_is_bounded(setup):
    client, _, _ = setup
    assert client.get("/api/targets/up/checks", params={"limit": 5000}).status_code == 422


def test_no_route_can_trigger_requests_to_arbitrary_destinations(setup):
    _, app, _ = setup
    for route in app.routes:
        if isinstance(route, APIRoute):
            assert route.methods <= {"GET", "HEAD"}, route.path
            params = {p.name for p in route.dependant.query_params + route.dependant.path_params}
            assert not params & {"url", "host", "target_url", "destination"}, route.path


def test_write_methods_are_rejected(setup):
    client, _, _ = setup
    assert client.post("/api/status", json={"url": "https://evil.test/"}).status_code == 405
    assert client.post("/api/targets/up/checks").status_code == 405


def test_remote_error_text_is_escaped(setup):
    client, _, storage = setup
    storage.add(
        [CheckResult("new", datetime.now(UTC), False, None, None, "<script>alert(1)</script>")]
    )
    html = client.get("/").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_security_headers(setup):
    client, _, _ = setup
    headers = client.get("/").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in headers["content-security-policy"]


def test_healthz(setup):
    client, _, _ = setup
    assert client.get("/healthz").json()["status"] == "ok"


def test_coverage_counts_from_when_monitoring_began(storage):
    settings = make_settings(interval=30)
    now = datetime.now(UTC)
    # Monitoring started 5 minutes ago: 10 checks, one per interval, no gaps.
    storage.add(
        [
            CheckResult("site", now - timedelta(seconds=30 * i), True, 200, 10.0, None)
            for i in range(10)
        ]
    )
    app = create_app(settings, storage, run_monitor=False)
    with TestClient(app) as client:
        window = client.get("/api/status").json()["targets"][0]["windows"]["24h"]
    assert window["total"] == 10
    assert window["coverage"] >= 99  # not 10 / 2880 checks of a full day


def test_coverage_drops_when_the_monitor_had_a_gap(storage):
    settings = make_settings(interval=30)
    now = datetime.now(UTC)
    # First check 10 minutes ago, then nothing until the last 5 minutes.
    storage.add([CheckResult("site", now - timedelta(minutes=10), True, 200, 10.0, None)])
    storage.add(
        [
            CheckResult("site", now - timedelta(seconds=30 * i), True, 200, 10.0, None)
            for i in range(10)
        ]
    )
    app = create_app(settings, storage, run_monitor=False)
    with TestClient(app) as client:
        window = client.get("/api/status").json()["targets"][0]["windows"]["24h"]
    assert 50 <= window["coverage"] <= 60
