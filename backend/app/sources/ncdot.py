"""NCDOT / DriveNC live road closures (live mode only).

DriveNC moved its public API in 2026 and the event schema is documented behind
a JavaScript page (https://drivenc.gov/help/endpoint/event). The endpoint URL and
key are configuration (NCDOT_EVENTS_URL / NCDOT_API_KEY). This parser accepts
GeoJSON FeatureCollections or JSON lists of events with lat/lon fields and keeps
events that describe a closure. Until configured, the source reports
"not configured" and closures are simply absent (never invented).
"""

from __future__ import annotations

import httpx

from app.config import settings

CLOSURE_WORDS = ("closed", "closure", "road closed", "lanes closed: all", "all lanes")


def _is_closure(props: dict) -> bool:
    text = " ".join(str(v) for k, v in props.items() if isinstance(v, (str, int, float))).lower()
    return any(w in text for w in CLOSURE_WORDS)


async def fetch_closures(client: httpx.AsyncClient) -> list[dict]:
    url = settings().ncdot_events_url
    if not url:
        raise RuntimeError("NCDOT_EVENTS_URL not configured")
    headers = {"X-API-Key": settings().ncdot_api_key} if settings().ncdot_api_key else {}
    r = await client.get(url, headers=headers)
    r.raise_for_status()
    js = r.json()
    out = []
    feats = js.get("features") if isinstance(js, dict) else None
    if feats is not None:
        for f in feats:
            p = f.get("properties") or {}
            if f.get("geometry") and _is_closure(p):
                out.append({"geometry": f["geometry"], "road": p.get("road") or p.get("roadName"),
                            "description": p.get("description") or p.get("reason")})
    elif isinstance(js, list):
        for ev in js:
            lat = ev.get("latitude") or ev.get("lat")
            lon = ev.get("longitude") or ev.get("lon")
            if lat is None or lon is None or not _is_closure(ev):
                continue
            out.append({"geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                        "road": ev.get("road") or ev.get("roadName"),
                        "description": ev.get("description") or ev.get("reason")})
    return out
