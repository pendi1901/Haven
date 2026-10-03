"""Location risk (spec §7.7 step 2) and crisis triggers (spec §7.6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np

from app.config import (
    LOCATION_ROAD_RADIUS_M, RIVER_SEVERITY, TRIGGER_AQI, TRIGGER_AQI_SENSITIVE, TRIGGER_FIRE_KM,
    TRIGGER_FORECAST_HOURS, TRIGGER_GAUGE_RADIUS_KM, TRIGGER_HEAT_INDEX_F, TRIGGER_ROAD_RADIUS_KM,
)
from app.geo.road_impact import CLOSED, FLOODED
from app.hazards.base import haversine_km
from app.models import GaugeStatus, HazardZone, LatLon, LocationRisk, Metric, Profile

if TYPE_CHECKING:
    from app.state import Haven, World

FLOOD_EVENTS = ("flood",)
FIRE_EVAC_EVENTS = ("fire warning", "evacuation", "civil emergency")


@dataclass
class TriggerResult:
    crisis: bool
    hazard: str | None
    reasons: list[str] = field(default_factory=list)
    flood: bool = False
    near_gauges: list[GaugeStatus] = field(default_factory=list)


def location_risk(ctx: "Haven", w: "World", p: LatLon) -> LocationRisk:
    imp = ctx.impact
    x, y, ridx, dist, W = imp.point_weights(p.lat, p.lon)
    ground = ctx.dem.sample_one(x, y)
    lv = w.impact.levels
    t = w.t
    we_now = we_fmax = None
    loc_flood_t: datetime | None = None
    flooded_now = False
    if ridx >= 0 and ground is not None:
        now = float(imp.water_at_samples(W[None, :], lv.now)[0])
        we_now = now if np.isfinite(now) else None
        fmax = we_now
        if lv.fc.shape[1]:
            fc = imp.water_at_samples(W[None, :], lv.fc)[0]
            if np.isfinite(fc).any():
                fmax = max(v for v in [we_now, float(np.nanmax(fc))] if v is not None)
                wet = np.nan_to_num(fc - ground, nan=-1) > 0
                if wet.any():
                    loc_flood_t = lv.fc_times[int(wet.argmax())]
        we_fmax = fmax
        if we_now is not None and we_now - ground > 0:
            flooded_now = True
            loc_flood_t = t

    cut_t, cut_now = _cut_off_time(ctx, w, p)
    times = [x for x in (loc_flood_t, cut_t) if x is not None]
    t_flood = min(times) if times else None
    return LocationRisk(
        ground_elev_m=ground, in_corridor=ridx >= 0, reach=imp.reaches_meta[ridx]["id"] if ridx >= 0 else None,
        distance_to_river_m=dist if np.isfinite(dist) else None, water_elev_now_m=we_now,
        water_elev_forecast_max_m=we_fmax, t_flood_here=t_flood, flooded_now=flooded_now, cut_off_now=cut_now,
    )


def _cut_off_time(ctx: "Haven", w: "World", p: LatLon) -> tuple[datetime | None, bool]:
    """Earliest official-forecast time every road within 300 m is flooded."""
    gd = ctx.router.graph("walk")
    x, y = ctx.impact.to_work.transform(p.lon, p.lat)
    idx = gd.tree.query_ball_point([x, y], LOCATION_ROAD_RADIUS_M)
    if not idx:
        return None, False
    times: list[datetime] = []
    for i in idx:
        n = int(gd.nodes[i])
        for _, _, d in gd.G.edges(n, data=True):
            j = ctx.impact.eid_index.get(("walk", d["eid"]))
            if j is None:
                return None, False  # an edge outside the evaluated corridor: stays dry
            st = w.impact.status_now[j]
            if st in (FLOODED, CLOSED):
                times.append(w.t)
                continue
            ff = w.impact.first_flood[j]
            if ff is None:
                return None, False
            times.append(ff)
    if not times:
        return None, False
    worst = max(times)
    return worst, worst <= w.t


def evaluate_triggers(ctx: "Haven", w: "World", p: LatLon, profile: Profile, risk: LocationRisk,
                      alerts_here: list[HazardZone], metrics: dict[str, Metric]) -> TriggerResult:
    t = w.t
    reasons: list[str] = []
    hazards: list[str] = []
    flood = False

    # 1. Inside an active NWS warning polygon (any event).
    for z in alerts_here:
        if z.is_warning:
            reasons.append(f"Inside an active {z.event} (NWS)")
            ev = (z.event or "").lower()
            if "tornado" in ev:
                hazards.append("tornado")
            elif "flood" in ev:
                hazards.append("flood")
                flood = True
            elif "thunderstorm" in ev or "extreme wind" in ev:
                hazards.append("severe_storm")
            elif "tropical" in ev or "hurricane" in ev:
                hazards.append("hurricane")
            elif "fire" in ev or "evacuation" in ev:
                hazards.append("wildfire")
            elif "winter" in ev or "ice" in ev or "blizzard" in ev:
                hazards.append("winter")
            elif "heat" in ev:
                hazards.append("heat")
            else:
                hazards.append("other")

    # 2. Gauge within 10 km at minor or above now, or official forecast crosses minor within 12 h.
    near = [g for g in w.gauges if haversine_km(p.lat, p.lon, g.lat, g.lon) <= TRIGGER_GAUGE_RADIUS_KM]
    horizon = t + timedelta(hours=TRIGGER_FORECAST_HOURS)
    for g in near:
        if RIVER_SEVERITY[g.category_now] >= RIVER_SEVERITY["minor"]:
            reasons.append(f"{g.short_name} is at {g.category_now} flood stage now")
            flood = True
        else:
            tm = g.time_to_category.get("minor")
            if tm and tm <= horizon:
                reasons.append(f"NOAA forecasts {g.short_name} to reach minor flood stage within 12 h")
                flood = True

    # 3. A road within 1 km flooded now or forecast to flood within 12 h.
    if _road_trigger(ctx, w, p, horizon):
        reasons.append("Roads within 1 km are flooded or forecast to flood within 12 h")
        flood = True
    if risk.t_flood_here and risk.t_flood_here <= horizon:
        flood = True
    if flood and "flood" not in hazards:
        hazards.insert(0, "flood")

    # 4. Air quality / heat thresholds.
    aqi = metrics.get("aqi")
    if aqi and aqi.value is not None and aqi.value >= (TRIGGER_AQI_SENSITIVE if profile.sensitive_health else TRIGGER_AQI):
        reasons.append(f"AQI {aqi.value:.0f} ({aqi.category})")
        hazards.append("smoke")
    hi = metrics.get("heat_index")
    if hi and hi.value is not None and hi.value >= TRIGGER_HEAT_INDEX_F:
        reasons.append(f"Heat index {hi.value:.0f}°F")
        hazards.append("heat")

    # 5. NHC forecast cone with TS conditions within 48 h.
    hit = ctx.modules["hurricane"].conditions_expected(p, t)
    if hit:
        reasons.append(f"Inside the NHC forecast cone for {hit[0]['name']}; tropical-storm-force winds possible within 48 h")
        hazards.append("hurricane")

    # 6. FIRMS detection within 15 km.
    near_fire = ctx.modules["wildfire"].nearest(p)
    if near_fire and near_fire[1] <= TRIGGER_FIRE_KM:
        reasons.append(f"Satellite fire detection {near_fire[1]:.1f} km away")
        hazards.append("wildfire")

    order = ["tornado", "flood", "wildfire", "severe_storm", "hurricane", "smoke", "heat", "winter", "other"]
    hazard = min(hazards, key=order.index) if hazards else None
    return TriggerResult(crisis=bool(reasons), hazard=hazard, reasons=reasons, flood=flood, near_gauges=near)


def _road_trigger(ctx: "Haven", w: "World", p: LatLon, horizon: datetime) -> bool:
    from shapely.geometry import Point
    x, y = ctx.impact.to_work.transform(p.lon, p.lat)
    tree = ctx.impact.edge_tree()
    geoms = ctx.impact._edge_geoms()
    for j in tree.query(Point(x, y), predicate="dwithin", distance=TRIGGER_ROAD_RADIUS_KM * 1000):
        i = geoms[j][0]
        if ctx.impact.edges[i]["graph"] != "drive":
            continue  # spec: "a road"; riverside footpaths alone do not start crisis mode
        if w.impact.status_now[i] == FLOODED:
            return True
        ff = w.impact.first_flood[i]
        if ff is not None and ff <= horizon:
            return True
    return False
