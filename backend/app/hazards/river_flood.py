"""River flood module: official NWPS observations + official river forecasts only
(spec §7.2). Feeds road impact (spec §7.3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from app.config import (
    FT_PER_M, GAUGE_OFFLINE_AFTER_H, RIVER_CATEGORIES, RIVER_SEVERITY,
)
from app.geo.road_impact import GaugeLevels, forecast_grid
from app.hazards.base import BaseModule, haversine_km, parse_dt
from app.models import ForecastPoint, GaugeStatus, HazardZone, LatLon, Metric, SeriesPoint, Source, TimeLayer
from app.sources.nwps import fetch_gauge

MAX_HORIZON_H = 120


def category_for(stage: float | None, thresholds: dict[str, float]) -> str:
    if stage is None:
        return "none"
    cat = "none"
    for c in RIVER_CATEGORIES:
        if c in thresholds and stage >= thresholds[c]:
            cat = c
    return cat


def crossing_times(series: list[tuple[datetime, float]], thresholds: dict[str, float],
                   observed: float | None, observed_at: datetime | None) -> dict[str, datetime | None]:
    """First time the official forecast series reaches each category threshold.

    Linear interpolation between consecutive official forecast points (anchored at
    the latest observation). If the river is already at/above a threshold, the
    observation time is returned.
    """
    out: dict[str, datetime | None] = {}
    for c in RIVER_CATEGORIES:
        h = thresholds.get(c)
        if h is None:
            continue
        if observed is not None and observed >= h:
            out[c] = observed_at
            continue
        out[c] = None
        for (t0, v0), (t1, v1) in zip(series[:-1], series[1:]):
            if v0 < h <= v1:
                f = (h - v0) / (v1 - v0) if v1 != v0 else 0
                out[c] = t0 + (t1 - t0) * f
                break
            if v0 >= h:
                out[c] = t0
                break
    return out


class RiverFlood(BaseModule):
    name = "river_flood"

    async def refresh(self, t: datetime) -> None:
        if self.replay:
            return
        for g in self.ctx.region.gauges:
            try:
                self.ctx.cache.put(f"nwps:{g.lid}", await fetch_gauge(self.ctx.client, g.lid))
            except Exception as ex:  # noqa: BLE001
                self.ctx.cache.fail(f"nwps:{g.lid}", f"{type(ex).__name__}: {ex}")

    # -- gauges -------------------------------------------------------------

    def gauges(self, t: datetime) -> list[GaugeStatus]:
        return [self._gauge(g, t) for g in self.ctx.region.gauges]

    def _gauge(self, g, t: datetime) -> GaugeStatus:
        meta = dict(self.ctx.gauge_meta.get(g.lid) or {})
        observed: list[tuple[datetime, float]] = []
        fc_points: list[tuple[datetime, float]] = []
        fc_issued = None
        fc_source = None
        stale = False
        note = None

        if self.replay:
            rd = self.ctx.replay
            reading = rd.reading_at(g.lid, t)
            observed = rd.observed_until(g.lid, t, hours=48)
            fc = rd.forecast_at(g.lid, t)
            if fc:
                fc_points = [(pt, v) for pt, v in fc["points"] if pt > t]
                fc_issued = fc["issued"]
                fc_source = "NWS Lower Mississippi River Forecast Center (archived product " + fc["product_id"] + ")"
        else:
            entry = self.ctx.cache.get(f"nwps:{g.lid}")
            data = entry.value or {}
            stale = entry.stale
            if data.get("meta"):
                meta.update({k: v for k, v in data["meta"].items() if v is not None})
            observed = [(parse_dt(p["t"]), p["stage_ft"]) for p in data.get("observed", [])]
            observed = [o for o in observed if o[0] <= t and o[0] >= t - timedelta(hours=48)]
            reading = observed[-1] if observed else None
            fc_points = [(parse_dt(p["t"]), p["stage_ft"]) for p in data.get("forecast", [])]
            fc_points = [p for p in fc_points if p[0] > t]
            fc_issued = parse_dt(data.get("forecast_issued"))
            fc_source = "NOAA NWPS official river forecast" if fc_points else None
            if entry.error and not data:
                note = "NWPS unavailable: " + entry.error

        thresholds = meta.get("thresholds_ft", {})
        obs_stage = reading[1] if reading else None
        obs_at = reading[0] if reading else None
        online = bool(obs_at and t - obs_at <= timedelta(hours=GAUGE_OFFLINE_AFTER_H))
        if obs_at and not online:
            note = f"Offline: no reading since {obs_at.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC. Showing last value."
        if obs_at is None:
            note = note or "No readings available."

        anchor = [(obs_at, obs_stage)] if (online and obs_stage is not None) else []
        series = anchor + fc_points
        if fc_points:
            peak_t, peak_v = max(fc_points, key=lambda p: p[1])
            peak_cat = category_for(peak_v, thresholds)
            ttc = crossing_times(series, thresholds, obs_stage if online else None, obs_at)
        else:
            peak_t = peak_v = None
            peak_cat = "none"
            ttc = {c: (obs_at if obs_stage is not None and online and c in thresholds and obs_stage >= thresholds[c] else None)
                   for c in RIVER_CATEGORIES if c in thresholds}
            if not note:
                note = ("Official forecast not available in replay at this time."
                        if self.replay else "No official forecast for this gauge.")

        return GaugeStatus(
            lid=g.lid, usgs_id=g.usgs_id, name=meta.get("name") or g.short_name, short_name=g.short_name,
            lat=meta.get("lat", 0.0), lon=meta.get("lon", 0.0),
            observed_stage_ft=obs_stage, observed_at=obs_at, thresholds_ft=thresholds,
            category_now=category_for(obs_stage, thresholds),
            forecast=[ForecastPoint(t=pt, stage_ft=v) for pt, v in fc_points],
            forecast_issued_at=fc_issued, forecast_source=fc_source,
            forecast_peak_ft=peak_v, forecast_peak_at=peak_t, forecast_peak_category=peak_cat,
            time_to_category=ttc, online=online, stale=stale or (obs_at is not None and not online),
            note=note, datum_navd88_ft=meta.get("datum_navd88_ft"),
            observed_series=[SeriesPoint(t=pt, stage_ft=v) for pt, v in observed],
            record_crest_ft=meta.get("record_crest_ft"),
        )

    # -- levels for road impact --------------------------------------------------

    def levels(self, t: datetime, gauges: list[GaugeStatus]) -> GaugeLevels:
        lids = self.ctx.impact.gauges
        by = {g.lid: g for g in gauges}
        now = np.full(len(lids), np.nan)
        stale = np.zeros(len(lids), dtype=bool)
        last_fc = t
        for i, lid in enumerate(lids):
            g = by.get(lid)
            if not g or g.observed_stage_ft is None or g.datum_navd88_ft is None:
                continue
            now[i] = (g.datum_navd88_ft + g.observed_stage_ft) / FT_PER_M
            stale[i] = not g.online
            if g.forecast:
                last_fc = max(last_fc, g.forecast[-1].t)
        horizon = min(MAX_HORIZON_H, max(0.0, (last_fc - t).total_seconds() / 3600))
        grid = forecast_grid(t, horizon) if horizon > 0 else []
        fc = np.full((len(lids), len(grid)), np.nan)
        if grid:
            gx = np.array([g.timestamp() for g in grid])
            for i, lid in enumerate(lids):
                g = by.get(lid)
                if not g or not g.forecast or g.datum_navd88_ft is None:
                    continue
                pts = ([(g.observed_at, g.observed_stage_ft)] if g.online and g.observed_stage_ft is not None else []) + \
                      [(p.t, p.stage_ft) for p in g.forecast]
                px = np.array([p[0].timestamp() for p in pts])
                py = np.array([(g.datum_navd88_ft + p[1]) / FT_PER_M for p in pts])
                fc[i] = np.interp(gx, px, py, left=np.nan, right=np.nan)
        return GaugeLevels(now=now, fc_times=grid, fc=fc, stale=stale)

    # -- interface -------------------------------------------------------------

    def zones(self, t: datetime) -> list[HazardZone]:
        w = self.ctx.world(t)
        out = []
        sev_now = max((RIVER_SEVERITY[g.category_now] for g in w.gauges), default=0)
        sev_fc = max((RIVER_SEVERITY[g.forecast_peak_category] for g in w.gauges), default=0)
        flooded_any = bool((w.impact.status_now >= 1).any())
        if w.impact.extent_now and (sev_now >= 1 or flooded_any):
            out.append(HazardZone(
                id="river_flood:now", hazard="river_flood", geometry=w.impact.extent_now,
                severity=max(sev_now, 2 if flooded_any else 1), layer=TimeLayer.NOW, valid_from=t, valid_to=None,
                source=Source.HAVEN,
                reason=f"Estimated flooded area: current official gauge levels overlaid on {self.ctx.dem.source} "
                       "(not an official inundation map)."))
        if w.impact.extent_forecast and sev_fc >= 2:
            peak = max((g for g in w.gauges if g.forecast_peak_at), key=lambda g: g.forecast_peak_ft or 0, default=None)
            out.append(HazardZone(
                id="river_flood:forecast", hazard="river_flood", geometry=w.impact.extent_forecast,
                severity=sev_fc, layer=TimeLayer.FORECAST, valid_from=t,
                valid_to=peak.forecast_peak_at if peak else None, source=Source.NWPS,
                reason=f"Estimated area at the official NOAA forecast crest, overlaid on {self.ctx.dem.source}."))
        return out

    def metrics_at(self, p: LatLon, t: datetime) -> list[Metric]:
        w = self.ctx.world(t)
        if not w.gauges:
            return []
        g = min(w.gauges, key=lambda g: haversine_km(p.lat, p.lon, g.lat, g.lon))
        dist = haversine_km(p.lat, p.lon, g.lat, g.lon)
        sev = RIVER_SEVERITY[g.category_now]
        cat_label = {"none": "Below flood stage", "action": "Action stage", "minor": "Minor flooding",
                     "moderate": "Moderate flooding", "major": "Major flooding"}[g.category_now]
        if g.forecast_peak_at and RIVER_SEVERITY[g.forecast_peak_category] > sev:
            when = g.time_to_category.get(g.forecast_peak_category) or g.forecast_peak_at
            advice = (f"NOAA forecasts {g.forecast_peak_category} flooding at this gauge "
                      f"(crest {g.forecast_peak_ft:.1f} ft). Avoid low roads near the river.")
            detail = f"Forecast crest {g.forecast_peak_ft:.1f} ft; {g.forecast_peak_category} stage around {when.isoformat()}"
        elif sev >= 2:
            advice = "The river is flooding. Stay off roads near the river and never drive into water."
            detail = None
        elif sev == 1:
            advice = "River is high but below flood stage. Keep an eye on official forecasts."
            detail = None
        else:
            advice = "River is within its banks."
            detail = None
        return [Metric(
            key="river", label=f"River · {g.short_name}", value=g.observed_stage_ft, unit="ft",
            category=cat_label, severity=sev, advice=advice, source=Source.USGS if self.replay else Source.NWPS,
            layer=TimeLayer.NOW, observed_or_valid_at=g.observed_at, stale=g.stale,
            detail=(detail or "") + (f" · {dist:.1f} km away" if dist else ""),
        )]
