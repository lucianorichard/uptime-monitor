"""Simulated websites for the demo (compose.demo.yaml). Not part of the monitor.

Each endpoint behaves like a site in a different condition, so the dashboard
shows realistic up/down/slow/timeout history within a few minutes.
"""

import asyncio
import random

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response

app = FastAPI(docs_url=None, redoc_url=None)


@app.get("/healthy", response_class=PlainTextResponse)
async def healthy():
    await asyncio.sleep(random.uniform(0.02, 0.08))
    return "ok"


@app.get("/slow", response_class=PlainTextResponse)
async def slow():
    await asyncio.sleep(random.uniform(0.4, 1.4))
    return "ok, eventually"


@app.get("/flaky")
async def flaky():
    await asyncio.sleep(random.uniform(0.05, 0.2))
    return Response("unavailable", status_code=503) if random.random() < 0.2 else Response("ok")


@app.get("/hangs-sometimes", response_class=PlainTextResponse)
async def hangs_sometimes():
    await asyncio.sleep(10 if random.random() < 0.15 else random.uniform(0.1, 0.3))
    return "ok"


@app.get("/broken")
async def broken():
    return Response("internal error", status_code=500)
