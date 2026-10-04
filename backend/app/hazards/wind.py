"""Wind: NWS grid forecast -> gusts and sustained wind now and the peak over the
next 24 h. Banded on NWS Wind Advisory / High Wind Warning criteria. Shares the
forecast fetched by the heat module (one NWS call for both)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.config import WIND_GUST_BANDS, WIND_SUSTAINED_BANDS, band
from app.hazards.base import BaseModule, compass, parse_dt
from app.models import LatLon, Metric, Source, TimeLayer

ADVICE = {
    "Light": "No wind risk.",
    "Breezy": "Secure light outdoor items.",
    "Windy": "Secure outdoor furniture and bins; take care driving high-profile vehicles.",
    "Strong": "Stay indoors if you can. Avoid wooded areas; falling branches and power outages are possible.",
    "High": "Stay indoors, away from windows. Avoid travel: trees and power lines may come down.",
    "Extreme": "Destructive winds. Shelter in an interior room on the lowest floor.",
}


_ORDER = [c for _, c, _ in WIND_GUST_BANDS]


def wind_band(gust: float | None, sustained: float | None) -> tuple[str, int]:
    """The worse of the gust and sustained bands (by category, so Breezy beats Light at equal severity)."""
    cands = [band(gust, WIND_GUST_BANDS)] if gust is not None else []
    if sustained is not None:
        cands.append(band(sustained, WIND_SUSTAINED_BANDS))
    return max(cands, key=lambda c: _ORDER.index(c[0])) if cands else ("Light", 0)


def _hourly_mph(s: str | None) -> float | None:
    """'10 mph' or '5 to 15 mph' -> the upper figure."""
    nums = re.findall(r"\d+(?:\.\d+)?", s or "")
    return float(nums[-1]) if nums else None


class Wind(BaseModule):
    name = "wind"

    def hourly(self) -> list[tuple[datetime, float | None, float | None]]:
        """(time, sustained mph, gust mph) per hour from the cached NWS forecast."""
        fc = self.ctx.cache.get("nws_forecast").value or {}
        gusts = {parse_dt(v["t"]): float(v["value"]) for v in fc.get("wind_gust_mph") or []}
        speeds = {parse_dt(v["t"]): float(v["value"]) for v in fc.get("wind_mph") or []}
        if not speeds:  # forecasts cached before the grid wind series existed
            for h in fc.get("hourly", []):
                v = _hourly_mph(h.get("wind"))
                if v is not None:
                    speeds[parse_dt(h["t"])] = v
        return [(t, speeds.get(t), gusts.get(t)) for t in sorted(speeds.keys() | gusts.keys())]

    def metrics_at(self, p: LatLon, t: datetime) -> list[Metric]:
        if self.replay:
            return [self.unavailable("wind", "Wind gusts", "mph", Source.NWS,
                                     "Not available in replay (no archived hourly forecast loaded).")]
        rows = self.hourly()
        e = self.ctx.cache.get("nws_forecast")
        if not rows:
            return [self.unavailable("wind", "Wind gusts", "mph", Source.NWS,
                                     "NWS forecast unavailable" + (f": {e.error}" if e.error else "."))]
        now_r = min(rows, key=lambda r: abs((r[0] - t).total_seconds()))
        nxt = [r for r in rows if t <= r[0] <= t + timedelta(hours=24)] or [now_r]
        peak_r = max(nxt, key=lambda r: wind_band(r[2], r[1])[1] * 1000 + (r[2] or r[1] or 0))
        heat = self.ctx.modules.get("heat")
        wdir = heat.wind_from_deg(now_r[0]) if heat else None
        return [self._metric("wind", "Wind gusts", now_r, e.stale,
                             "NWS forecast for this hour" + (f" · from the {compass(wdir)}" if wdir is not None else "")),
                self._metric("wind_max24", "Wind gusts, next 24 h", peak_r, e.stale, "Peak of the NWS forecast")]

    @staticmethod
    def _metric(key: str, label: str, r: tuple[datetime, float | None, float | None], stale: bool,
                detail: str) -> Metric:
        t, sustained, gust = r
        cat, sev = wind_band(gust, sustained)
        value = gust if gust is not None else sustained
        if gust is not None and sustained is not None:
            detail = f"Sustained {sustained:.0f} mph · {detail}"
        elif gust is None:
            detail = f"Sustained wind (no gust forecast) · {detail}"
        return Metric(key=key, label=label, value=round(value), unit="mph", category=cat, severity=sev,
                      advice=ADVICE[cat], source=Source.NWS, layer=TimeLayer.FORECAST, observed_or_valid_at=t,
                      stale=stale, detail=detail)
