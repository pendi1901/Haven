"""NWS active alerts (spec §5). Zone-based alerts without polygons get the union of
their affected zones that intersect the region."""

from __future__ import annotations

import httpx
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

from app.config import RegionConfig, settings

_zone_cache: dict[str, dict | None] = {}


async def _zone_geometry(client: httpx.AsyncClient, url: str) -> dict | None:
    if url in _zone_cache:
        return _zone_cache[url]
    try:
        r = await client.get(url)
        geom = r.json().get("geometry") if r.status_code == 200 else None
    except httpx.HTTPError:
        geom = None
    _zone_cache[url] = geom
    return geom


async def fetch_alerts(client: httpx.AsyncClient, region: RegionConfig) -> list[dict]:
    r = await client.get(f"{settings().nws_base}/alerts/active", params={"area": region.state},
                         headers={"Accept": "application/geo+json"})
    r.raise_for_status()
    bbox = box(*region.bbox)
    out = []
    for f in r.json().get("features", []):
        p = f["properties"]
        geom = f.get("geometry")
        if geom is None:
            parts = []
            for zurl in p.get("affectedZones") or []:
                g = await _zone_geometry(client, zurl)
                if g:
                    s = shape(g)
                    if s.intersects(bbox):
                        parts.append(s)
            if not parts:
                continue
            geom = mapping(unary_union(parts).simplify(0.002))
        elif not shape(geom).intersects(bbox):
            continue
        out.append({
            "id": p.get("id") or f.get("id"),
            "event": p.get("event"),
            "headline": p.get("headline"),
            "description": p.get("description"),
            "instruction": p.get("instruction"),
            "severity": p.get("severity"),
            "certainty": p.get("certainty"),
            "urgency": p.get("urgency"),
            "message_type": p.get("messageType"),
            "sender": p.get("senderName"),
            "sent": p.get("sent"),
            "effective": p.get("effective"),
            "onset": p.get("onset"),
            "expires": p.get("expires"),
            "ends": p.get("ends"),
            "parameters": {k: v for k, v in (p.get("parameters") or {}).items()
                           if k in ("VTEC", "flashFloodDamageThreat", "tornadoDamageThreat", "flashFloodDetection")},
            "geometry": geom,
        })
    return out
