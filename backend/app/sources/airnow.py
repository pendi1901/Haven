"""AirNow current AQI observations and official AQI forecasts (spec §5)."""

from __future__ import annotations

import httpx

from app.config import settings


async def fetch_airnow(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    key = settings().airnow_api_key
    if not key:
        raise RuntimeError("AIRNOW_API_KEY not configured")
    base = settings().airnow_base
    common = {"format": "application/json", "latitude": lat, "longitude": lon, "distance": 50, "API_KEY": key}
    obs = await client.get(f"{base}/aq/observation/latLong/current/", params=common)
    obs.raise_for_status()
    fc = await client.get(f"{base}/aq/forecast/latLong/", params=common)
    fc.raise_for_status()
    return {
        "observations": [
            {"area": o.get("ReportingArea"), "lat": o.get("Latitude"), "lon": o.get("Longitude"),
             "parameter": o.get("ParameterName"), "aqi": o.get("AQI"),
             "category": (o.get("Category") or {}).get("Name"),
             "date": o.get("DateObserved"), "hour": o.get("HourObserved"), "tz": o.get("LocalTimeZone")}
            for o in obs.json()
        ],
        "forecasts": [
            {"area": f.get("ReportingArea"), "parameter": f.get("ParameterName"), "aqi": f.get("AQI"),
             "category": (f.get("Category") or {}).get("Name"), "date": f.get("DateForecast"),
             "action_day": f.get("ActionDay"), "discussion": f.get("Discussion")}
            for f in fc.json()
        ],
    }
