"""Air quality: AirNow current + official AQI forecast; PurpleAir (EPA-corrected)
as a denser fallback (spec §7.4)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.config import AQI_BANDS, band
from app.hazards.base import BaseModule, haversine_km
from app.models import LatLon, Metric, Source, TimeLayer
from app.sources.airnow import fetch_airnow
from app.sources.purpleair import fetch_purpleair

ADVICE = {
    "Good": "Air quality is good.",
    "Moderate": "Unusually sensitive people should limit long outdoor exertion.",
    "Unhealthy for Sensitive Groups": "If you have heart or lung conditions, are older, or have kids with you, limit time outdoors.",
    "Unhealthy": "Limit time outdoors. Keep windows closed.",
    "Very Unhealthy": "Stay indoors with windows closed. Run an air purifier if you have one.",
    "Hazardous": "Stay indoors. Go to a clean-air shelter if you cannot keep indoor air clean.",
}


class AirQuality(BaseModule):
    name = "air_quality"

    async def refresh(self, t: datetime) -> None:
        if self.replay:
            return
        lat, lon = self.ctx.region.center
        for name, coro in (("airnow", lambda: fetch_airnow(self.ctx.client, lat, lon)),
                           ("purpleair", lambda: fetch_purpleair(self.ctx.client, self.ctx.region))):
            try:
                self.ctx.cache.put(name, await coro())
            except Exception as ex:  # noqa: BLE001
                self.ctx.cache.fail(name, f"{type(ex).__name__}: {ex}")

    def aqi_at(self, p: LatLon) -> tuple[float | None, str | None, Source | None, datetime | None]:
        an = self.ctx.cache.get("airnow").value
        if an and an.get("observations"):
            obs = an["observations"]
            nearest_area = min(obs, key=lambda o: haversine_km(p.lat, p.lon, o["lat"], o["lon"]))["area"]
            area_obs = [o for o in obs if o["area"] == nearest_area and o.get("aqi") is not None]
            if area_obs:
                worst = max(area_obs, key=lambda o: o["aqi"])
                return float(worst["aqi"]), f"{worst['parameter']} · {nearest_area}", Source.AIRNOW, \
                    self.ctx.cache.get("airnow").fetched_at
        pa = self.ctx.cache.get("purpleair").value
        if pa:
            near = [s for s in pa if haversine_km(p.lat, p.lon, s["lat"], s["lon"]) <= 5]
            if near:
                s = min(near, key=lambda s: haversine_km(p.lat, p.lon, s["lat"], s["lon"]))
                return float(s["aqi"]), "PM2.5 (EPA-corrected PurpleAir)", Source.PURPLEAIR, \
                    self.ctx.cache.get("purpleair").fetched_at
        return None, None, None, None

    def metrics_at(self, p: LatLon, t: datetime) -> list[Metric]:
        if self.replay:
            return [self.unavailable("aqi", "Air quality", "AQI", Source.AIRNOW,
                                     "Not available in replay (no archived AQI loaded).")]
        aqi, detail, src, at = self.aqi_at(p)
        if aqi is None:
            e = self.ctx.cache.get("airnow")
            why = "Add AIRNOW_API_KEY to enable." if e.error and "not configured" in e.error else \
                "No AirNow reporting area nearby right now."
            return [self.unavailable("aqi", "Air quality", "AQI", Source.AIRNOW, why)]
        cat, sev = band(aqi, AQI_BANDS)
        out = [Metric(key="aqi", label="Air quality", value=aqi, unit="AQI", category=cat, severity=sev,
                      advice=ADVICE[cat], source=src, layer=TimeLayer.NOW, observed_or_valid_at=at,
                      stale=self.ctx.cache.get("airnow").stale, detail=detail)]
        an = self.ctx.cache.get("airnow").value or {}
        today = datetime.now(timezone.utc).date().isoformat()
        fcs = [f for f in an.get("forecasts", []) if f.get("aqi") not in (None, -1) and (f.get("date") or "") >= today]
        if fcs:
            worst = max(fcs, key=lambda f: f["aqi"])
            fcat, fsev = band(worst["aqi"], AQI_BANDS)
            out.append(Metric(key="aqi_forecast", label="Air quality forecast", value=float(worst["aqi"]), unit="AQI",
                              category=fcat, severity=fsev, advice=f"Official AirNow forecast for {worst['date']}: {fcat}.",
                              source=Source.AIRNOW, layer=TimeLayer.FORECAST,
                              observed_or_valid_at=datetime.fromisoformat(worst["date"]).replace(tzinfo=timezone.utc),
                              detail=worst.get("parameter")))
        return out
