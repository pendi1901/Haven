"""UV: EPA hourly UV index forecast -> peak UV and the hours it is high (spec §7.4)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import UV_BANDS, band
from app.hazards.base import BaseModule
from app.models import LatLon, Metric, Source, TimeLayer
from app.sources.epa_uv import fetch_uv

ADVICE = {
    "Low": "No protection needed for most people.",
    "Moderate": "Wear sunscreen if you are outside for long.",
    "High": "Seek shade at midday; wear sunscreen and a hat.",
    "Very High": "Minimize sun exposure from late morning to mid-afternoon.",
    "Extreme": "Avoid the sun at midday; unprotected skin burns in minutes.",
}


class UV(BaseModule):
    name = "uv"

    async def refresh(self, t: datetime) -> None:
        if self.replay:
            return
        try:
            self.ctx.cache.put("epa_uv", await fetch_uv(self.ctx.client, self.ctx.region))
        except Exception as ex:  # noqa: BLE001
            self.ctx.cache.fail("epa_uv", f"{type(ex).__name__}: {ex}")

    def metrics_at(self, p: LatLon, t: datetime) -> list[Metric]:
        if self.replay:
            return [self.unavailable("uv", "UV index", "", Source.EPA, "Not available in replay.")]
        e = self.ctx.cache.get("epa_uv")
        rows = e.value or []
        if not rows:
            return [self.unavailable("uv", "UV index", "", Source.EPA,
                                     "EPA UV forecast unavailable" + (f": {e.error}" if e.error else "."))]
        tz = ZoneInfo(self.ctx.region.timezone)
        pts = [(datetime.fromisoformat(r["local_time"]).replace(tzinfo=tz), r["uv"]) for r in rows if r["uv"] is not None]
        peak_t, peak = max(pts, key=lambda x: x[1])
        high = [pt for pt, v in pts if v >= 6]
        cat, sev = band(peak, UV_BANDS)
        if high:
            start_hour = high[0].hour % 12 or 12
            end_hour = high[-1].hour % 12 or 12
            hours = f"{start_hour} {high[0]:%p}–{end_hour} {high[-1]:%p}"
        else:
            hours = None
        return [Metric(key="uv", label="UV index (peak)", value=float(peak), unit="", category=cat, severity=sev,
                       advice=ADVICE[cat] + (f" High UV {hours}." if hours else ""), source=Source.EPA,
                       layer=TimeLayer.FORECAST, observed_or_valid_at=peak_t, stale=e.stale,
                       detail=f"EPA forecast for {self._place()}")]

    def _place(self) -> str:
        r = self.ctx.region
        return f"ZIP {r.uv_zip}" if r.uv_zip else f"{r.uv_city}, {r.state}"
