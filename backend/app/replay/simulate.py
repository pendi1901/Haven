"""Simulated Walnut Creek flood (Raleigh) for demos.

    python -m app.replay.simulate

Writes a made-up storm into the replay archive format (backend/data/raleigh/replay),
so the replay clock, verdicts, routing and navigation work on it unchanged:

- usgs/<id>.json        15-minute stages for every Raleigh gauge with a USGS id
- rvf_forecasts.json    river forecasts issued every few hours for the Walnut Creek
                        gauges (in reality none of them has an official forecast)
- alerts.json           a Flood Watch, a Flash Flood Warning and a Flood Warning

The storm: remnants of a tropical system stall over southwest Raleigh and Cary on
Tuesday evening. Walnut Creek rises fast overnight and crests near its records early
Wednesday, upstream first. Early forecasts underestimate the crest, as real ones often
do, so a route that looks fine at 9 PM can be cut off by midnight.

Nothing here is an official product. Every file is marked simulated, and the app
labels the scenario, its alerts and its forecasts as simulated. Output is
deterministic: rerunning it gives the same files.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import geopandas as gpd
import shapely
from shapely.geometry import box, mapping

from app.config import REGIONS, WORK_CRS, RegionConfig
from app.replay.loader import replay_dir
from app.sources.nwps import load_gauge_meta

log = logging.getLogger(__name__)
TZ = ZoneInfo("America/New_York")
DAY = {"Tue": 29, "Wed": 30, "Thu": 1}  # Sept 29 - Oct 1, 2026


def local(day: str, hh: int, mm: int = 0) -> datetime:
    month = 10 if day == "Thu" else 9
    return datetime(2026, month, DAY[day], hh, mm, tzinfo=TZ).astimezone(timezone.utc)


# Crest per gauge: (base stage ft, crest ft, crest time, rise hours, fall hours). Stages
# are on each gauge's own datum; Lake Johnson (JHSN7) reports lake elevation (ft NAVD88).
# Crests sit between NOAA's moderate stage and the gauge's record crest.
CRESTS = {
    "BKJN7": (0.96, 10.6, local("Wed", 1, 0), 2.5, 7.0),      # major 10, record 10.79
    "TRLN7": (2.10, 13.0, local("Wed", 2, 30), 3.0, 8.0),     # minor 12 (between the dams; shown only)
    "JHSN7": (343.16, 346.3, local("Wed", 6, 0), 5.0, 30.0),  # Lake Johnson pool rises ~3 ft
    "WAWN7": (0.55, 14.6, local("Wed", 3, 0), 3.0, 8.0),      # moderate 14
    "WSSN7": (1.55, 12.5, local("Wed", 3, 45), 3.0, 9.0),     # minor 9
    "WRLN7": (3.95, 11.6, local("Wed", 4, 30), 3.2, 9.0),     # moderate 11, record 10.98
    "WALN7": (2.18, 15.8, local("Wed", 5, 30), 3.5, 10.0),    # moderate 14, record 17.03
}
# Crabtree Creek gets rain too but stays below action stage.
CRABTREE_CREST_AT = local("Wed", 4, 0)

# Forecast issuances (local) and how much of the eventual rise each one foresaw.
FORECASTS = [
    (local("Tue", 8), 0.30), (local("Tue", 14), 0.55), (local("Tue", 20), 0.80), (local("Tue", 23), 0.95),
    (local("Wed", 2), 1.0), (local("Wed", 5), 1.0), (local("Wed", 8), 1.0), (local("Wed", 14), 1.0),
    (local("Wed", 20), 1.0), (local("Thu", 2), 1.0),
]
FORECAST_HOURS = 48
STEP = timedelta(minutes=15)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _shape(t: datetime, crest_at: datetime, rise_h: float, fall_h: float) -> float:
    """0..1 hydrograph: steep rise, slower recession."""
    h = (t - crest_at).total_seconds() / 3600
    w = rise_h if h < 0 else fall_h
    return math.exp(-((h / w) ** 2))


def _crests(region: RegionConfig) -> dict[str, tuple[float, float, datetime, float, float]]:
    meta = load_gauge_meta(region)
    out = dict(CRESTS)
    for g in region.gauges:
        if g.lid in out or not g.usgs_id or g.base_flow_stage_ft is None:
            continue
        action = (meta.get(g.lid) or {}).get("thresholds_ft", {}).get("action")
        rise = 3.0 if action is None else min(3.0, 0.4 * (action - g.base_flow_stage_ft))
        out[g.lid] = (g.base_flow_stage_ft, g.base_flow_stage_ft + rise, CRABTREE_CREST_AT, 4.0, 12.0)
    return out


def _truth(c, t: datetime, wiggle: bool = True) -> float:
    base, crest, at, rise_h, fall_h = c
    v = base + (crest - base) * _shape(t, at, rise_h, fall_h)
    if wiggle:  # small, deterministic sensor noise
        v += 0.03 * math.sin(t.timestamp() / 3600 * 2.7 + crest)
    return v


def gauge_series(region: RegionConfig) -> dict[str, list[tuple[str, float]]]:
    out = {}
    start = region.replay_start - timedelta(days=1)
    for lid, c in _crests(region).items():
        pts, t = [], start
        while t <= region.replay_end:
            pts.append((_iso(t), round(_truth(c, t), 2)))
            t += STEP
        out[lid] = pts
    return out


def forecasts(region: RegionConfig) -> list[dict]:
    """Forecasts start from the reading at issuance and add `share` of the rise to come."""
    out = []
    for lid, c in _crests(region).items():
        if lid not in CRESTS or lid == "TRLN7":  # Trailwood is between the dams: not modeled
            continue
        for issued, share in FORECASTS:
            obs = _truth(c, issued)
            pts = []
            for h in range(1, FORECAST_HOURS + 1):
                t = issued + timedelta(hours=h)
                v = obs + share * (_truth(c, t, wiggle=False) - _truth(c, issued, wiggle=False))
                pts.append((_iso(t), round(max(c[0], v), 2)))
            out.append({"lid": lid, "issued": _iso(issued),
                        "product_id": f"SIM-RVF-{issued:%Y%m%d%H%M}", "points": pts, "simulated": True})
    out.sort(key=lambda f: (f["lid"], f["issued"]))
    return out


# -- alerts --------------------------------------------------------------------------

BANNER = ("SIMULATED ALERT FOR A HAVEN DEMONSTRATION. It was not issued by the National Weather "
          "Service, and no flood is happening.\n\n")

WATCH_TEXT = BANNER + """* WHAT...Flooding caused by excessive rainfall is possible.

* WHERE...Wake County, including the cities of Raleigh and Cary.

* WHEN...From this evening through Wednesday afternoon.

* IMPACTS...Excessive runoff may result in flooding of rivers, creeks, streams, and other
low-lying and flood-prone locations. Walnut Creek and Crabtree Creek may rise out of their banks.

* ADDITIONAL DETAILS...The remnants of a tropical system are expected to stall over the
Triangle tonight, with 4 to 7 inches of rain and locally higher amounts."""
WATCH_DO = ("You should monitor later forecasts and be alert for possible Flood Warnings. Those living "
            "in areas prone to flooding should be prepared to take action should flooding develop.")

FFW_TEXT = BANNER + """Flash Flood Warning for south Raleigh and east Cary along Walnut Creek.

* AT 958 PM EDT, radar indicated heavy rain over the warned area. Between 3 and 5 inches of
rain have fallen, and another 2 inches are possible. Flash flooding is ongoing or expected to
begin shortly.

HAZARD...Flash flooding caused by heavy rain.

SOURCE...Radar and automated stream gauges.

IMPACT...Flash flooding of small creeks and streams, urban areas, highways, streets and
underpasses.

* SOME LOCATIONS THAT WILL EXPERIENCE FLASH FLOODING INCLUDE...
Buck Jones Road, Lake Johnson, South Wilmington Street, South State Street, Rose Lane,
Sunnybrook Road, Garner Road and Bailey Drive."""
FFW_DO = ("Move to higher ground now. Act quickly to protect your life. Turn around, don't drown when "
          "encountering flooded roads. Most flood deaths occur in vehicles.")

FLW_TEXT = BANNER + """* WHAT...Moderate flooding is forecast.

* WHERE...Walnut Creek from South Wilmington Street to Sunnybrook Drive.

* WHEN...Until Thursday morning.

* IMPACTS...At 14.0 feet at Sunnybrook Drive, water covers Sunnybrook Road and Rose Lane near
the creek and reaches apartments in the floodplain. Garner Road and Bailey Drive flood near
the creek crossings.

* ADDITIONAL DETAILS...The creek is expected to crest near 15.8 feet early Wednesday morning,
then fall below flood stage Wednesday evening."""
FLW_DO = "Do not drive cars through flooded areas. Turn around, don't drown."


def _walnut_area(region: RegionConfig, buffer_m: float, east_of: float | None = None) -> dict:
    rivers = gpd.read_file(region.data_dir / "osm" / "rivers.geojson")
    walnut = rivers[rivers["name"] == "Walnut Creek"]
    if east_of is not None:
        walnut = walnut.clip(box(east_of, -90, 180, 90))
    area = shapely.union_all(walnut.to_crs(WORK_CRS).geometry.values).buffer(buffer_m).simplify(60)
    return mapping(gpd.GeoSeries([area], crs=WORK_CRS).to_crs("EPSG:4326").iloc[0])


def alerts(region: RegionConfig) -> dict:
    w, s, e, n = region.bbox
    watch_area = mapping(box(w - 0.05, s - 0.05, e + 0.05, n + 0.05))
    ffw = ("RAH-FF.W.9001", local("Tue", 22), local("Wed", 4), local("Wed", 8))  # key, issue, expire, in effect until
    flw = ("RAH-FL.W.9002", local("Wed", 1), local("Thu", 8), region.replay_end)
    watch = ("RAH-FA.A.9003", region.replay_start, None, local("Wed", 14))

    def polygon(key, issue, expire, event, ph, sig, text, do, geometry):
        return {"kind": "polygon", "id": f"{key}-SIM", "event_key": key, "event": event, "phenomena": ph,
                "significance": sig, "eventid": int(key[-4:]), "status": "NEW", "issue": _iso(issue),
                "expire": _iso(expire), "polygon_begin": _iso(issue), "polygon_end": _iso(expire),
                "product_id": f"{issue:%Y%m%d%H%M}-SIM", "is_emergency": False, "is_pds": False,
                "geometry": geometry, "description": text, "instruction": do, "simulated": True}

    polygons = [
        polygon(ffw[0], ffw[1], ffw[2], "Flash Flood Warning", "FF", "W", FFW_TEXT, FFW_DO,
                _walnut_area(region, 900)),
        polygon(flw[0], flw[1], flw[2], "Flood Warning", "FL", "W", FLW_TEXT, FLW_DO,
                _walnut_area(region, 450, east_of=-78.665)),
    ]
    zone_events = [{
        "kind": "zone", "id": watch[0], "event": "Flood Watch", "phenomena": "FA", "significance": "A",
        "eventid": 9003, "wfo": "RAH", "issue": _iso(watch[1]), "product_issue": _iso(watch[1]), "ugcs": [],
        "geometry": watch_area, "description": WATCH_TEXT, "instruction": WATCH_DO, "simulated": True,
    }]
    samples, t = [], region.replay_start
    while t <= region.replay_end:
        active = [k for k, issue, _, until in (ffw, flw, watch) if issue <= t < until or (until == region.replay_end and t >= issue)]
        samples.append({"t": _iso(t), "active": sorted(active)})
        t += STEP
    return {"polygons": polygons, "zone_events": zone_events, "zone_samples": samples, "simulated": True}


def write(region: RegionConfig) -> None:
    if not region.replay_simulated:
        raise SystemExit(f"{region.key} is not configured as a simulated scenario")
    d = replay_dir(region)
    (d / "usgs").mkdir(parents=True, exist_ok=True)
    ids = {g.lid: g.usgs_id for g in region.gauges}
    series = gauge_series(region)
    for lid, pts in series.items():
        (d / "usgs" / f"{ids[lid]}.json").write_text(json.dumps(
            {"usgs_id": ids[lid], "lid": lid, "parameter": "00065", "unit": "ft", "points": pts, "simulated": True}))
    fcs = forecasts(region)
    (d / "rvf_forecasts.json").write_text(json.dumps(fcs))
    (d / "alerts.json").write_text(json.dumps(alerts(region)))
    log.info("simulated scenario written to %s: %d gauges, %d forecasts", d, len(series), len(fcs))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="raleigh")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    write(REGIONS[args.region])


if __name__ == "__main__":
    main()
