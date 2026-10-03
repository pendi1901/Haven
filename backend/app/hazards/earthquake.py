"""Earthquakes: USGS feed, quakes within 200 km in the last 24 h. Informational
(spec §7.4)."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.hazards.base import BaseModule, haversine_km, parse_dt
from app.models import LatLon, Metric, Source, TimeLayer
from app.sources.usgs_quakes import fetch_quakes

RADIUS_KM = 200


class Earthquake(BaseModule):
    name = "earthquake"

    async def refresh(self, t: datetime) -> None:
        if self.replay:
            return
        try:
            self.ctx.cache.put("quakes", await fetch_quakes(self.ctx.client))
        except Exception as ex:  # noqa: BLE001
            self.ctx.cache.fail("quakes", f"{type(ex).__name__}: {ex}")

    def nearby(self, p: LatLon, t: datetime) -> list[dict]:
        if self.replay:
            return []
        out = []
        for q in self.ctx.cache.get("quakes").value or []:
            qt = parse_dt(q["t"])
            if qt and qt >= t - timedelta(hours=24) and haversine_km(p.lat, p.lon, q["lat"], q["lon"]) <= RADIUS_KM:
                out.append(q)
        return out

    def metrics_at(self, p: LatLon, t: datetime) -> list[Metric]:
        if self.replay:
            return [self.unavailable("quakes", "Earthquakes", "", Source.USGS, "Not available in replay.")]
        e = self.ctx.cache.get("quakes")
        qs = self.nearby(p, t)
        if not qs:
            return [Metric(key="quakes", label="Earthquakes", value=0, unit="in 24 h", category="None", severity=0,
                           advice="No earthquakes within 200 km in the last 24 hours.", source=Source.USGS,
                           layer=TimeLayer.NOW, observed_or_valid_at=e.fetched_at, stale=e.stale)]
        big = max(qs, key=lambda q: q.get("mag") or 0)
        d = haversine_km(p.lat, p.lon, big["lat"], big["lon"])
        sev = 1 if (big.get("mag") or 0) >= 5 and d <= 50 else 0
        return [Metric(key="quakes", label="Earthquakes", value=len(qs), unit="in 24 h",
                       category=f"Largest M{big['mag']:.1f}", severity=sev,
                       advice="Informational. After shaking, check for gas leaks and damage.", source=Source.USGS,
                       layer=TimeLayer.NOW, observed_or_valid_at=parse_dt(big["t"]), stale=e.stale,
                       detail=f"{big.get('place')} · {d:.0f} km away")]
