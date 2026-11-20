"""One HTTP check: GET the URL, time it, classify the outcome."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from . import __version__
from .config import Target

USER_AGENT = f"uptime-monitor/{__version__} (+self-hosted)"


@dataclass(frozen=True)
class CheckResult:
    target: str
    checked_at: datetime
    ok: bool
    status_code: int | None
    response_ms: float | None
    error: str | None


def _short(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return text if len(text) <= 160 else text[:157] + "..."


async def check_target(client: httpx.AsyncClient, target: Target) -> CheckResult:
    """GET the target and record what happened.

    response_ms is the time until the response *headers* arrive (DNS, TCP,
    TLS and server processing included); the body is not downloaded, so a
    large page does not distort the latency or waste bandwidth.

    A check succeeds when a response arrives within the timeout and its status
    code is in the target's expected set. Anything else is a failed check.
    """
    checked_at = datetime.now(UTC)
    request = client.build_request(
        "GET",
        target.url,
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(target.timeout_seconds),
    )
    start = time.perf_counter()
    try:
        # httpx timeouts apply per phase (connect, read...); wait_for adds a hard
        # wall-clock limit so a server trickling bytes cannot stall a check.
        response = await asyncio.wait_for(
            client.send(request, stream=True, follow_redirects=target.follow_redirects),
            timeout=target.timeout_seconds,
        )
    except (TimeoutError, httpx.TimeoutException):
        return CheckResult(
            target.name, checked_at, False, None, None, f"timeout after {target.timeout_seconds:g}s"
        )
    except httpx.ConnectError as exc:
        return CheckResult(
            target.name, checked_at, False, None, None, f"connection failed: {_short(exc)}"
        )
    except httpx.HTTPError as exc:
        return CheckResult(
            target.name, checked_at, False, None, None, f"{type(exc).__name__}: {_short(exc)}"
        )

    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    await response.aclose()
    ok = target.accepts(response.status_code)
    error = (
        None
        if ok
        else (f"unexpected status {response.status_code} (expected {target.expected_label})")
    )
    return CheckResult(target.name, checked_at, ok, response.status_code, elapsed_ms, error)
