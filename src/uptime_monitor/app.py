"""Read-only web dashboard and JSON API.

Every route is a GET that reads stored results for *configured* targets. There
is deliberately no route that accepts a URL or triggers a request to an
arbitrary destination.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import __version__
from .availability import window_stats
from .config import Settings, Target
from .monitor import Monitor
from .storage import Storage

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.filters["pct"] = lambda v: "no data" if v is None else f"{v:.2f}%"
TEMPLATES.env.filters["ms"] = lambda v: "-" if v is None else f"{v:.0f} ms"
TEMPLATES.env.filters["when"] = lambda v: "-" if not v else str(v)[:19].replace("T", " ") + " UTC"
WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}
BAR_CHECKS = 60
CHART_W, CHART_H = 720, 140


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _state(latest, interval: int, now: datetime) -> str:
    if latest is None:
        return "no-data"
    if now - _parse_time(latest["checked_at"]) > timedelta(seconds=3 * interval):
        return "stale"  # the monitor has not checked recently; do not trust "up"
    return "up" if latest["ok"] else "down"


def _chart(rows) -> dict:
    """Server-side SVG geometry for a latency chart (oldest to newest)."""
    rows = list(reversed(rows))
    latencies = [r["response_ms"] for r in rows if r["response_ms"] is not None]
    top = max(latencies, default=1.0) * 1.15 or 1.0
    n = max(len(rows) - 1, 1)
    points, failures = [], []
    for i, r in enumerate(rows):
        x = round(i * CHART_W / n, 1)
        if r["response_ms"] is not None:
            points.append(f"{x},{round(CHART_H - r['response_ms'] / top * CHART_H, 1)}")
        if not r["ok"]:
            failures.append(x)
    return {
        "points": " ".join(points),
        "failures": failures,
        "max_ms": round(top),
        "width": CHART_W,
        "height": CHART_H,
    }


def summarize(settings: Settings, storage: Storage, target: Target, now: datetime) -> dict:
    recent = storage.recent(target.name, BAR_CHECKS)
    latest = recent[0] if recent else None
    stats = {}
    for label, delta in WINDOWS.items():
        rows = storage.since(target.name, now - delta)
        stats[label] = window_stats(
            [bool(r["ok"]) for r in rows],
            [r["response_ms"] for r in rows if r["response_ms"] is not None],
            delta.total_seconds(),
            settings.interval_seconds,
        )
    return {
        "target": target,
        "state": _state(latest, settings.interval_seconds, now),
        "latest": dict(latest) if latest else None,
        "bars": [dict(r) for r in reversed(recent)],
        "stats": stats,
        "chart": _chart(recent),
    }


def create_app(
    settings: Settings,
    storage: Storage | None = None,
    *,
    run_monitor: bool = True,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = storage or Storage(settings.database)
        app.state.storage = store
        app.state.monitor = Monitor(settings, store, transport)
        task = asyncio.create_task(app.state.monitor.run_forever()) if run_monitor else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if storage is None:
                store.close()

    app = FastAPI(
        title="Uptime Monitor",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:"
            )
        return response

    def configured(name: str) -> Target:
        target = settings.target(name)
        if target is None:
            raise HTTPException(status_code=404, detail="Unknown target")
        return target

    def summaries(request: Request) -> list[dict]:
        now = datetime.now(UTC)
        return [summarize(settings, request.app.state.storage, t, now) for t in settings.targets]

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        items = summaries(request)
        counts = {
            s: sum(1 for i in items if i["state"] == s) for s in ("up", "down", "stale", "no-data")
        }
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "items": items,
                "counts": counts,
                "settings": settings,
                "last_run": request.app.state.monitor.last_run,
                "version": __version__,
            },
        )

    @app.get("/targets/{name}", response_class=HTMLResponse)
    def target_page(request: Request, name: str):
        target = configured(name)
        now = datetime.now(UTC)
        return TEMPLATES.TemplateResponse(
            request,
            "target.html",
            {
                "item": summarize(settings, request.app.state.storage, target, now),
                "checks": [dict(r) for r in request.app.state.storage.recent(name, 100)],
                "settings": settings,
                "version": __version__,
            },
        )

    @app.get("/api/status")
    def api_status(request: Request):
        return {
            "interval_seconds": settings.interval_seconds,
            "last_run": request.app.state.monitor.last_run,
            "targets": [
                {
                    "name": s["target"].name,
                    "url": s["target"].url,
                    "state": s["state"],
                    "latest": s["latest"],
                    "windows": {k: asdict(v) for k, v in s["stats"].items()},
                }
                for s in summaries(request)
            ],
        }

    @app.get("/api/targets/{name}/checks")
    def api_checks(request: Request, name: str, limit: int = Query(100, ge=1, le=1000)):
        configured(name)
        return [dict(r) for r in request.app.state.storage.recent(name, limit)]

    @app.get("/healthz")
    def healthz(request: Request):
        return {"status": "ok", "last_run": request.app.state.monitor.last_run}

    return app
