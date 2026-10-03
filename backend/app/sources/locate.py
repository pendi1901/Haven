"""Build a live "point region" around any US location: NWS gives the place name,
state and time zone; NWPS gives the river gauges nearby. No prepared data needed."""

from __future__ import annotations

import math

import httpx

from app.config import GaugeConfig, RegionConfig, settings

GAUGE_RADIUS_KM = 15.0
MAX_GAUGES = 6
HALF_SPAN = (0.15, 0.12)  # lon, lat degrees (~13 km)


class OutsideCoverage(Exception):
    pass


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def point_key(lat: float, lon: float) -> str:
    # ~5 km cells so nearby requests share one context (and one set of pollers).
    return f"pt_{round(lat * 20) / 20:.2f}_{round(lon * 20) / 20:.2f}"


async def point_region(client: httpx.AsyncClient, lat: float, lon: float) -> RegionConfig:
    s = settings()
    r = await client.get(f"{s.nws_base}/points/{lat:.4f},{lon:.4f}")
    if r.status_code == 404:
        raise OutsideCoverage("Haven's live data comes from the US National Weather Service, "
                              "which doesn't cover this location.")
    r.raise_for_status()
    p = r.json()["properties"]
    rel = (p.get("relativeLocation") or {}).get("properties") or {}
    city, state = rel.get("city") or "Your area", rel.get("state") or ""
    dx, dy = HALF_SPAN
    gauges: list[GaugeConfig] = []
    try:
        g = await client.get(f"{s.nwps_base}/gauges", params={
            "bbox.xmin": lon - dx, "bbox.ymin": lat - dy, "bbox.xmax": lon + dx, "bbox.ymax": lat + dy,
            "srid": "EPSG_4326"})
        g.raise_for_status()
        found = [(x, _km(lat, lon, x["latitude"], x["longitude"])) for x in g.json().get("gauges", [])]
        found = sorted((f for f in found if f[1] <= GAUGE_RADIUS_KM and (f[0].get("pedts") or {}).get("observed")),
                       key=lambda f: f[1])[:MAX_GAUGES]
        gauges = [GaugeConfig(x["lid"], x.get("usgsId") or None, (x.get("name") or x["lid"]).split(",")[0])
                  for x, _ in found]
    except httpx.HTTPError:
        pass  # gauges are optional; alerts and forecasts still work
    return RegionConfig(
        key=point_key(lat, lon), name=f"{city}, {state}".strip(", "),
        bbox=(lon - dx, lat - dy, lon + dx, lat + dy), center=(lat, lon),
        timezone=p.get("timeZone") or "America/New_York", state=state, uv_zip="", uv_city=city,
        gauges=tuple(gauges), reaches=(), point=True,
    )
