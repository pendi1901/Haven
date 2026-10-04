"""GET /api/geocode: find a place or address when GPS isn't available.

Uses OpenStreetMap Nominatim, US results only, with its usage policy respected:
an identifying User-Agent, at most one request per second, cached answers, and
searches only on explicit submit (never per keystroke)."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.sources.base import make_client

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UNAVAILABLE = "Place search is unavailable right now. Use your current location or tap a spot on the map instead."
BUSY = "Place search is busy. Wait a few seconds and try again, or tap a spot on the map."
_cache: dict[str, list[dict]] = {}
_lock = asyncio.Lock()
_last = 0.0
_warned_ua = False


def _check_user_agent() -> None:
    global _warned_ua
    if _warned_ua:
        return
    _warned_ua = True
    if "example.com" in settings().nws_user_agent:
        log.warning("NWS_USER_AGENT uses a placeholder contact (%s); Nominatim and NWS may block it. "
                    "Set it to a real contact email.", settings().nws_user_agent)


def _parse(rows) -> list[dict]:
    out, seen = [], set()
    for x in rows:
        lat, lon = float(x["lat"]), float(x["lon"])
        k = (round(lat, 5), round(lon, 5))
        if k in seen:
            continue
        seen.add(k)
        out.append({"label": x["display_name"].replace(", United States", ""), "lat": lat, "lon": lon})
    return out


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError:
        raise HTTPException(422, "bbox must be west,south,east,north") from None
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise HTTPException(422, "bbox must be west,south,east,north")
    return w, s, e, n


@router.get("/geocode")
async def geocode(q: str = Query(..., min_length=2, max_length=200),
                  bbox: str | None = Query(None, description="west,south,east,north: only places inside "
                                                             "(replay scenarios cover one area)")):
    global _last
    _check_user_agent()
    area = _parse_bbox(bbox)
    q_norm = " ".join(q.lower().split())
    key = q_norm if area is None else f"{q_norm}|{','.join(f'{v:.4f}' for v in area)}"
    if key in _cache:
        return _cache[key]
    params = {"q": q_norm, "format": "jsonv2", "limit": 5, "countrycodes": "us", "addressdetails": 0}
    if area:
        w, s, e, n = area
        params.update(viewbox=f"{w},{n},{e},{s}", bounded=1)
    async with _lock:
        # Another request may have filled the cache while this one waited.
        if key in _cache:
            return _cache[key]
        wait = 1.0 - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        _last = time.monotonic()
        try:
            async with make_client(timeout=15) as client:
                r = await client.get(NOMINATIM, params=params)
        except httpx.HTTPError as e:
            log.warning("Nominatim request failed: %r", e)
            raise HTTPException(503, UNAVAILABLE) from e
    if r.status_code in (403, 429):
        log.warning("Nominatim refused the request (HTTP %s)", r.status_code)
        raise HTTPException(503, BUSY)
    if r.status_code != 200:
        raise HTTPException(502, UNAVAILABLE)
    try:
        out = _parse(r.json())
    except (ValueError, KeyError, TypeError) as e:
        log.warning("Nominatim returned an unexpected response: %r", e)
        raise HTTPException(502, UNAVAILABLE) from e
    _cache[key] = out
    return out
