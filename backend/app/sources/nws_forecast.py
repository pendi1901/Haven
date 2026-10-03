"""NWS point forecast: hourly forecast + raw grid data (official heat index,
apparent temperature, wind direction) for about 7 days (spec §5)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings

_points_cache: dict[str, dict] = {}


async def _point(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    key = f"{lat:.3f},{lon:.3f}"
    if key not in _points_cache:
        r = await client.get(f"{settings().nws_base}/points/{lat:.4f},{lon:.4f}")
        r.raise_for_status()
        _points_cache[key] = r.json()["properties"]
    return _points_cache[key]


def _expand_grid_series(values: list[dict], horizon_h: int = 72) -> list[dict]:
    """NWS grid values carry ISO 8601 'validTime' like 2026-10-03T18:00:00+00:00/PT3H."""
    out = []
    for v in values:
        if v.get("value") is None:
            continue
        start_s, _, dur = v["validTime"].partition("/")
        start = datetime.fromisoformat(start_s)
        m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?", dur)
        hours = (int(m.group(1) or 0) * 24 + int(m.group(2) or 0)) if m else 1
        for h in range(max(1, hours)):
            out.append({"t": (start + timedelta(hours=h)).isoformat(), "value": v["value"]})
    now = datetime.now(timezone.utc) - timedelta(hours=1)
    end = now + timedelta(hours=horizon_h)
    return [o for o in out if now <= datetime.fromisoformat(o["t"]) <= end]


async def fetch_forecast(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    pt = await _point(client, lat, lon)
    hourly_r = await client.get(pt["forecastHourly"])
    hourly_r.raise_for_status()
    grid_r = await client.get(pt["forecastGridData"])
    grid_r.raise_for_status()
    periods = hourly_r.json()["properties"]["periods"][:72]
    grid = grid_r.json()["properties"]

    def series(key: str) -> list[dict]:
        return _expand_grid_series((grid.get(key) or {}).get("values") or [])

    return {
        "location": (pt.get("relativeLocation") or {}).get("properties", {}),
        "office": pt.get("gridId"),
        "updated": hourly_r.json()["properties"].get("updateTime"),
        "hourly": [
            {
                "t": p["startTime"],
                "temp_f": p.get("temperature"),
                "rh": (p.get("relativeHumidity") or {}).get("value"),
                "pop": (p.get("probabilityOfPrecipitation") or {}).get("value"),
                "wind": p.get("windSpeed"),
                "wind_dir": p.get("windDirection"),
                "short": p.get("shortForecast"),
            }
            for p in periods
        ],
        # Grid values are in degC; converted by the consumer.
        "heat_index_c": series("heatIndex"),
        "apparent_c": series("apparentTemperature"),
        "wind_dir_deg": series("windDirection"),
    }
