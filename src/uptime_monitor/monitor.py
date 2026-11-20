"""Periodic task: check every configured target once per interval."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx

from .checker import CheckResult, check_target
from .config import Settings
from .storage import Storage

log = logging.getLogger("uptime_monitor")
MAX_CONCURRENT_CHECKS = 10


class Monitor:
    def __init__(self, settings: Settings, storage: Storage,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.storage = storage
        self._transport = transport  # tests inject httpx.MockTransport
        self.last_run: datetime | None = None

    async def run_once(self) -> list[CheckResult]:
        """Check all targets concurrently, store the results, prune old ones."""
        limit = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)

        async def one(client, target):
            async with limit:
                return await check_target(client, target)

        async with httpx.AsyncClient(transport=self._transport, http2=False) as client:
            results = await asyncio.gather(*(one(client, t) for t in self.settings.targets))

        now = datetime.now(UTC)
        self.storage.add(list(results))
        self.storage.prune(now - timedelta(days=self.settings.retention_days))
        self.last_run = now
        for r in results:
            if not r.ok:
                log.warning("%s DOWN: %s", r.target, r.error)
        return list(results)

    async def run_forever(self) -> None:
        """Run on a fixed schedule. The next run is planned from the previous
        start time, so slow checks do not make the schedule drift."""
        interval = self.settings.interval_seconds
        next_run = time.monotonic()
        while True:
            try:
                await self.run_once()
            except Exception:  # keep monitoring even if one cycle fails (e.g. disk full)
                log.exception("monitoring cycle failed")
            next_run += interval
            delay = next_run - time.monotonic()
            if delay < 0:  # a cycle overran the interval: skip missed slots, do not burst
                next_run = time.monotonic()
                delay = 0
            await asyncio.sleep(delay)
