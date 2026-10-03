"""GET /api/stream: Server-Sent Events. Pushes `state_updated` whenever a live
poller sees changed data (spec §10). Replay is client-clocked, so the stream only
sends heartbeats there."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.state import get_haven

router = APIRouter(prefix="/api")


@router.get("/stream")
async def stream(request: Request):
    ctx = get_haven()
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    ctx.subscribers.add(q)

    async def gen():
        try:
            yield {"event": "hello", "data": json.dumps({"data_mode": ctx.mode, "version": ctx.version})}
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20)
                    yield {"event": msg["event"], "data": json.dumps(msg)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            ctx.subscribers.discard(q)

    return EventSourceResponse(gen())
