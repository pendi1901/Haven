"""USGS earthquake feed (past day) (spec §5)."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.config import settings


async def fetch_quakes(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(settings().quakes_url)
    r.raise_for_status()
    out = []
    for f in r.json().get("features", []):
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        out.append({
            "id": f.get("id"), "mag": p.get("mag"), "place": p.get("place"),
            "t": datetime.fromtimestamp(p["time"] / 1000, timezone.utc).isoformat(),
            "lat": lat, "lon": lon, "url": p.get("url"),
        })
    return out
