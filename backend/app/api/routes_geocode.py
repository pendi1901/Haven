"""GET /api/geocode: find a place or address when GPS isn't available.

Uses OpenStreetMap Nominatim, US results only, with its usage policy respected:
an identifying User-Agent, at most one request per second, cached answers, and
searches only on explicit submit (never per keystroke)."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Query

from app.sources.base import make_client

router = APIRouter(prefix="/api")
NOMINATIM = "https://nominatim.openstreetmap.org/search"
_cache: dict[str, list[dict]] = {}
_lock = asyncio.Lock()
_last = 0.0


@router.get("/geocode")
async def geocode(q: str = Query(..., min_length=2, max_length=200)):
    global _last
    key = " ".join(q.lower().split())
    if key in _cache:
        return _cache[key]
    async with _lock:
        wait = 1.0 - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        _last = time.monotonic()
        async with make_client(timeout=15) as client:
            r = await client.get(NOMINATIM, params={"q": q, "format": "jsonv2", "limit": 5, "countrycodes": "us",
                                                    "addressdetails": 0})
    if r.status_code != 200:
        raise HTTPException(502, "Place search is unavailable right now. Tap a spot on the map instead.")
    out = [{"label": x["display_name"].replace(", United States", ""), "lat": float(x["lat"]), "lon": float(x["lon"])}
           for x in r.json()]
    _cache[key] = out
    return out
