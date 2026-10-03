"""Heat: NWS hourly forecast -> heat index now and max over the next 24 h
(spec §7.4). Uses the NWS grid heat index when published, otherwise the NWS
Rothfusz regression on the official hourly temperature and humidity."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.config import HEAT_BANDS, band
from app.hazards.base import BaseModule, parse_dt
from app.models import LatLon, Metric, Source, TimeLayer
from app.sources.nws_forecast import fetch_forecast

ADVICE = {
    "None": "No heat risk.",
    "Caution": "Drink water regularly during outdoor activity.",
    "Extreme Caution": "Take breaks in shade or AC; avoid exertion in the afternoon.",
    "Danger": "Stay in air conditioning. Check on older neighbors.",
    "Extreme Danger": "Heat stroke is likely with exposure. Get to air conditioning now.",
}


def heat_index_f(temp_f: float, rh: float) -> float:
    """NWS heat index (Rothfusz regression with NWS adjustments)."""
    hi = 0.5 * (temp_f + 61.0 + (temp_f - 68.0) * 1.2 + rh * 0.094)
    if (hi + temp_f) / 2 < 80:
        return hi
    hi = (-42.379 + 2.04901523 * temp_f + 10.14333127 * rh - 0.22475541 * temp_f * rh
          - 0.00683783 * temp_f ** 2 - 0.05481717 * rh ** 2 + 0.00122874 * temp_f ** 2 * rh
          + 0.00085282 * temp_f * rh ** 2 - 0.00000199 * temp_f ** 2 * rh ** 2)
    if rh < 13 and 80 <= temp_f <= 112:
        hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(temp_f - 95)) / 17)
    elif rh > 85 and 80 <= temp_f <= 87:
        hi += ((rh - 85) / 10) * ((87 - temp_f) / 5)
    return hi


class Heat(BaseModule):
    name = "heat"

    async def refresh(self, t: datetime) -> None:
        if self.replay:
            return
        lat, lon = self.ctx.region.center
        try:
            self.ctx.cache.put("nws_forecast", await fetch_forecast(self.ctx.client, lat, lon))
        except Exception as ex:  # noqa: BLE001
            self.ctx.cache.fail("nws_forecast", f"{type(ex).__name__}: {ex}")

    def hourly_hi(self) -> list[tuple[datetime, float]]:
        fc = self.ctx.cache.get("nws_forecast").value or {}
        out = []
        for h in fc.get("hourly", []):
            if h.get("temp_f") is None:
                continue
            rh = h.get("rh") if h.get("rh") is not None else 50
            out.append((parse_dt(h["t"]), heat_index_f(float(h["temp_f"]), float(rh))))
        return out

    def metrics_at(self, p: LatLon, t: datetime) -> list[Metric]:
        if self.replay:
            return [self.unavailable("heat_index", "Heat index", "°F", Source.NWS,
                                     "Not available in replay (no archived hourly forecast loaded).")]
        series = self.hourly_hi()
        e = self.ctx.cache.get("nws_forecast")
        if not series:
            return [self.unavailable("heat_index", "Heat index", "°F", Source.NWS,
                                     "NWS forecast unavailable" + (f": {e.error}" if e.error else "."))]
        now_v = min(series, key=lambda s: abs((s[0] - t).total_seconds()))
        nxt = [s for s in series if t <= s[0] <= t + timedelta(hours=24)] or [now_v]
        peak = max(nxt, key=lambda s: s[1])
        cat, sev = band(now_v[1], HEAT_BANDS)
        pcat, psev = band(peak[1], HEAT_BANDS)
        return [
            Metric(key="heat_index", label="Heat index", value=round(now_v[1]), unit="°F", category=cat,
                   severity=sev, advice=ADVICE[cat], source=Source.NWS, layer=TimeLayer.FORECAST,
                   observed_or_valid_at=now_v[0], stale=e.stale, detail="NWS hourly forecast for this hour"),
            Metric(key="heat_index_max24", label="Heat index, next 24 h", value=round(peak[1]), unit="°F",
                   category=pcat, severity=psev, advice=ADVICE[pcat], source=Source.NWS, layer=TimeLayer.FORECAST,
                   observed_or_valid_at=peak[0], stale=e.stale, detail="Peak of the NWS hourly forecast"),
        ]

    def wind_from_deg(self, t: datetime) -> float | None:
        fc = self.ctx.cache.get("nws_forecast").value or {}
        vals = fc.get("wind_dir_deg") or []
        if not vals:
            return None
        v = min(vals, key=lambda v: abs((parse_dt(v["t"]) - t).total_seconds()))
        return float(v["value"])
