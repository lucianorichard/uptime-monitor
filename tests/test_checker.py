import asyncio

import httpx

from uptime_monitor.checker import USER_AGENT, check_target
from uptime_monitor.config import parse_config


def target(**overrides):
    return parse_config(
        {
            "monitor": {"interval_seconds": 30},
            "targets": [
                {"name": "site", "url": "https://site.test/", "timeout_seconds": 1, **overrides}
            ],
        }
    ).targets[0]


def run(handler, **overrides):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await check_target(client, target(**overrides))

    return asyncio.run(go())


def test_success_records_status_and_latency():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, text="hello")

    r = run(handler)
    assert (r.ok, r.status_code, r.error) == (True, 200, None)
    assert r.response_ms is not None and r.response_ms >= 0
    assert r.checked_at.tzinfo is not None
    assert seen["ua"] == USER_AGENT


def test_server_error_is_a_failed_check_with_reason():
    r = run(lambda req: httpx.Response(503))
    assert (r.ok, r.status_code) == (False, 503)
    assert r.error == "unexpected status 503 (expected 200-399)"


def test_redirects_are_not_followed_by_default():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(301, headers={"Location": "https://elsewhere.test/"})

    r = run(handler)
    assert (r.ok, r.status_code) == (True, 301)  # 3xx is within the default 200-399
    assert calls == ["https://site.test/"]  # never contacted elsewhere.test


def test_strict_expected_status_turns_a_redirect_into_a_failure():
    r = run(lambda req: httpx.Response(302, headers={"Location": "/login"}), expected_status=200)
    assert not r.ok and "unexpected status 302" in r.error


def test_redirects_can_be_followed_when_configured():
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(301, headers={"Location": "https://site.test/home"})
        return httpx.Response(200)

    r = run(handler, follow_redirects=True)
    assert (r.ok, r.status_code) == (True, 200)


def test_connection_error():
    def handler(request):
        raise httpx.ConnectError("Name or service not known", request=request)

    r = run(handler)
    assert (r.ok, r.status_code, r.response_ms) == (False, None, None)
    assert r.error.startswith("connection failed: Name or service not known")


def test_httpx_timeout():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    r = run(handler)
    assert (r.ok, r.error) == (False, "timeout after 1s")


def test_hard_wall_clock_timeout_on_a_hanging_server():
    async def handler(request):
        await asyncio.sleep(5)  # longer than the 1s timeout
        return httpx.Response(200)

    r = run(handler)
    assert (r.ok, r.error) == (False, "timeout after 1s")


def test_other_protocol_errors_are_reported():
    def handler(request):
        raise httpx.RemoteProtocolError("Server disconnected", request=request)

    r = run(handler)
    assert not r.ok and r.error == "RemoteProtocolError: Server disconnected"
