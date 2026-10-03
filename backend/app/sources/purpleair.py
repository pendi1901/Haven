"""PurpleAir sensors (optional). PM2.5 is corrected with the EPA US-wide
correction (Barkjohn et al. 2021) before converting to AQI."""

from __future__ import annotations

import httpx

from app.config import RegionConfig, settings

# EPA PM2.5 AQI breakpoints (2024 revision): (C_lo, C_hi, I_lo, I_hi)
PM25_BREAKPOINTS = [
    (0.0, 9.0, 0, 50),
    (9.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 125.4, 151, 200),
    (125.5, 225.4, 201, 300),
    (225.5, 325.4, 301, 500),
]


def epa_correct(pm25_cf1: float, rh: float) -> float:
    return max(0.0, 0.524 * pm25_cf1 - 0.0862 * rh + 5.75)


def pm25_to_aqi(c: float) -> int:
    c = int(c * 10) / 10  # truncate to 0.1 per EPA
    for clo, chi, ilo, ihi in PM25_BREAKPOINTS:
        if clo <= c <= chi:
            return round((ihi - ilo) / (chi - clo) * (c - clo) + ilo)
    return 500


async def fetch_purpleair(client: httpx.AsyncClient, region: RegionConfig) -> list[dict]:
    key = settings().purpleair_api_key
    if not key:
        raise RuntimeError("PURPLEAIR_API_KEY not configured")
    w, s, e, n = region.bbox
    r = await client.get(
        f"{settings().purpleair_base}/sensors",
        headers={"X-API-Key": key},
        params={"fields": "latitude,longitude,pm2.5_cf_1,humidity,last_seen", "location_type": 0,
                "nwlng": w, "nwlat": n, "selng": e, "selat": s, "max_age": 3600},
    )
    r.raise_for_status()
    js = r.json()
    fields = js["fields"]
    out = []
    for row in js["data"]:
        d = dict(zip(fields, row))
        if d.get("pm2.5_cf_1") is None or d.get("humidity") is None:
            continue
        corrected = epa_correct(d["pm2.5_cf_1"], d["humidity"])
        out.append({"lat": d["latitude"], "lon": d["longitude"], "pm25": round(corrected, 1),
                    "aqi": pm25_to_aqi(corrected), "last_seen": d.get("last_seen")})
    return out
