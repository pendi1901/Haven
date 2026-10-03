"""Wildfire: NASA FIRMS detections within 50 km -> zones; distance and direction to
the nearest; wind from the NWS forecast for an upwind/downwind note (display only)
(spec §7.4)."""

from __future__ import annotations

import math
from datetime import datetime

from shapely.geometry import Point, mapping

from app.geo.roads import bearing
from app.hazards.base import BaseModule, compass, haversine_km, parse_dt
from app.models import HazardZone, LatLon, Metric, Source, TimeLayer
from app.sources.firms import fetch_fires

ZONE_RADIUS_KM = 50
PIXEL_RADIUS_DEG = 0.004  # ~375 m VIIRS pixel


class Wildfire(BaseModule):
    name = "wildfire"

    async def refresh(self, t: datetime) -> None:
        if self.replay:
            return
        try:
            self.ctx.cache.put("firms", await fetch_fires(self.ctx.client, self.ctx.region))
        except Exception as ex:  # noqa: BLE001
            self.ctx.cache.fail("firms", f"{type(ex).__name__}: {ex}")

    def detections(self) -> list[dict]:
        return [] if self.replay else (self.ctx.cache.get("firms").value or [])

    def zones(self, t: datetime) -> list[HazardZone]:
        lat0, lon0 = self.ctx.region.center
        out = []
        for i, f in enumerate(self.detections()):
            if haversine_km(lat0, lon0, f["lat"], f["lon"]) > ZONE_RADIUS_KM:
                continue
            out.append(HazardZone(
                id=f"firms:{i}", hazard="wildfire",
                geometry=mapping(Point(f["lon"], f["lat"]).buffer(PIXEL_RADIUS_DEG, 8)), severity=2,
                layer=TimeLayer.NOW, valid_from=parse_dt(f["acq"]) or t, valid_to=None, source=Source.FIRMS,
                reason=f"Satellite fire detection (VIIRS, confidence {f.get('confidence')})."))
        return out

    def nearest(self, p: LatLon) -> tuple[dict, float] | None:
        fires = self.detections()
        if not fires:
            return None
        f = min(fires, key=lambda f: haversine_km(p.lat, p.lon, f["lat"], f["lon"]))
        return f, haversine_km(p.lat, p.lon, f["lat"], f["lon"])

    def metrics_at(self, p: LatLon, t: datetime) -> list[Metric]:
        if self.replay:
            return [self.unavailable("fire", "Active fires", "", Source.FIRMS, "Not available in replay.")]
        e = self.ctx.cache.get("firms")
        if e.value is None:
            why = "Add FIRMS_MAP_KEY to enable." if e.error and "not configured" in e.error else "FIRMS unavailable."
            return [self.unavailable("fire", "Active fires", "", Source.FIRMS, why)]
        near = self.nearest(p)
        if not near or near[1] > ZONE_RADIUS_KM:
            return [Metric(key="fire", label="Active fires", value=0, unit="within 50 km", category="None",
                           severity=0, advice="No satellite fire detections nearby in the last 24 h.",
                           source=Source.FIRMS, layer=TimeLayer.NOW, observed_or_valid_at=e.fetched_at, stale=e.stale)]
        f, d = near
        brg = bearing(p.lon, p.lat, f["lon"], f["lat"])
        detail = f"Nearest {d:.1f} km {compass(brg)}"
        wind_from = self.ctx.modules["heat"].wind_from_deg(t)
        if wind_from is not None:
            # Wind blowing from the fire toward you means you are downwind.
            diff = abs((wind_from - brg + 180) % 360 - 180)
            detail += " · you are " + ("downwind (smoke likely)" if diff < 45 else "not downwind")
        sev = 3 if d <= 5 else 2 if d <= 15 else 1
        return [Metric(key="fire", label="Active fires", value=round(d, 1), unit="km to nearest",
                       category="Fire nearby" if d <= 15 else "Fire in region", severity=sev,
                       advice="Be ready to leave if officials order it. Keep windows closed if smoky.",
                       source=Source.FIRMS, layer=TimeLayer.NOW, observed_or_valid_at=parse_dt(f["acq"]),
                       stale=e.stale, detail=detail)]
