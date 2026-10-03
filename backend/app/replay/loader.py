"""Helene replay data: download once, then answer "what was known at time t".

Archives used (all official products, retrieved from public archives):
- USGS Water Data API: 15-minute gage height for the configured gauges.
- NWS Lower Mississippi RFC river forecasts (RVF SHEF products) as issued during
  the event, from the Iowa Environmental Mesonet (IEM) NWS text archive.
- NWS warning polygons (storm-based warnings) and zone-based watches/warnings, with
  their verbatim product text, from the IEM VTEC archive.
- NHC 5-day forecast cones and track points for the storm, from the NHC GIS archive.

No-leakage rule (spec §11): every query takes `t` and only returns data whose
observation/issuance time is <= t.
"""

from __future__ import annotations

import bisect
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.config import RegionConfig, settings
from app.replay.shef import parse_rvf

log = logging.getLogger(__name__)


def replay_dir(region: RegionConfig) -> Path:
    return region.data_dir / "replay"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Fetchers (run by `python -m app.prepare`)
# ---------------------------------------------------------------------------


def fetch_usgs(region: RegionConfig, client: httpx.Client, force: bool = False) -> None:
    out_dir = replay_dir(region) / "usgs"
    out_dir.mkdir(parents=True, exist_ok=True)
    start = region.replay_start - timedelta(days=1)
    end = region.replay_end
    for g in region.gauges:
        if not g.usgs_id:
            continue
        path = out_dir / f"{g.usgs_id}.json"
        if path.exists() and not force:
            continue
        pts: list[tuple[str, float]] = []
        url = f"{settings().usgs_water_base}/collections/continuous/items"
        params = {
            "monitoring_location_id": f"USGS-{g.usgs_id}",
            "parameter_code": "00065",
            "datetime": f"{_iso(start)}/{_iso(end)}",
            "limit": 10000,
            "f": "json",
        }
        r = client.get(url, params=params)
        r.raise_for_status()
        for f in r.json()["features"]:
            p = f["properties"]
            if p.get("value") in (None, ""):
                continue
            pts.append((_iso(_parse_dt(p["time"])), float(p["value"])))
        pts.sort()
        path.write_text(json.dumps({"usgs_id": g.usgs_id, "lid": g.lid, "parameter": "00065",
                                    "unit": "ft", "points": pts}))
        log.info("USGS %s: %d readings", g.usgs_id, len(pts))


def fetch_rvf(region: RegionConfig, client: httpx.Client, force: bool = False) -> None:
    path = replay_dir(region) / "rvf_forecasts.json"
    if path.exists() and not force:
        return
    raw_dir = replay_dir(region) / "rvf_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    lids = {g.lid for g in region.gauges}
    iem = settings().iem_base
    day = (region.replay_start - timedelta(days=2)).date()
    last = region.replay_end.date()
    forecasts = []
    while day <= last:
        for pil in region.replay_rvf_pils:
            r = client.get(f"{iem}/api/1/nws/afos/list.json", params={"pil": pil, "date": day.isoformat()})
            r.raise_for_status()
            for row in r.json().get("data", []):
                pid = row["product_id"]
                if region.replay_rvf_office and region.replay_rvf_office not in pid:
                    continue
                txt_path = raw_dir / f"{pid}.txt"
                if not txt_path.exists():
                    tr = client.get(f"{iem}/api/1/nwstext/{pid}")
                    tr.raise_for_status()
                    txt_path.write_text(tr.text)
                for fc in parse_rvf(txt_path.read_text(), lids):
                    forecasts.append({
                        "lid": fc["lid"],
                        "issued": _iso(fc["issued"]),
                        "product_id": pid,
                        "points": [(_iso(t), v) for t, v in fc["points"]],
                    })
        day += timedelta(days=1)
    forecasts.sort(key=lambda f: (f["lid"], f["issued"], f["product_id"]))
    path.write_text(json.dumps(forecasts))
    log.info("RVF: %d forecast series", len(forecasts))


_PRODUCT_SPLIT = re.compile(r"\n\$\$\s*\n?")


def _split_alert_text(text: str, vtec_key: str | None) -> dict:
    """Pull the segment for one VTEC event and split description / instructions."""
    segments = _PRODUCT_SPLIT.split(text)
    seg = segments[0]
    if vtec_key:
        for s in segments:
            if vtec_key in s:
                seg = s
                break
    lines = seg.splitlines()
    # Drop WMO header, UGC, VTEC, and the BULLETIN banner lines.
    body: list[str] = []
    for ln in lines:
        s = ln.strip()
        if re.match(r"^\d{3}\s*$", s) or re.match(r"^[A-Z]{4}\d{2} [A-Z]{4} \d{6}", s):
            continue
        if re.match(r"^/[OTEX]\.", s) or re.match(r"^/[A-Z0-9]{5}\.[0-9N]\.", s) or re.match(r"^[A-Z]{2}[CZ]\d{3}.*-\s*$", s):
            continue
        if re.match(r"^[A-Z]{3}[A-Z0-9]{3}$", s):  # AFOS PIL line
            continue
        if s.startswith("BULLETIN") or s.startswith("URGENT -"):
            continue
        body.append(ln.rstrip())
    joined = "\n".join(body).strip()
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    instruction = None
    m = re.search(r"PRECAUTIONARY/PREPAREDNESS ACTIONS\.\.\.\s*\n(.*?)(\n&&|\Z)", joined, re.S)
    if m:
        instruction = m.group(1).strip()
        joined = joined[: m.start()].strip()
    joined = re.split(r"\n&&\s*\n", joined)[0]
    joined = re.sub(r"\nLAT\.\.\.LON.*", "", joined, flags=re.S).strip()
    return {"description": joined[:6000], "instruction": instruction[:3000] if instruction else None}


def fetch_alerts(region: RegionConfig, client: httpx.Client, force: bool = False) -> None:
    path = replay_dir(region) / "alerts.json"
    if path.exists() and not force:
        return
    iem = settings().iem_base
    text_dir = replay_dir(region) / "alert_text"
    text_dir.mkdir(parents=True, exist_ok=True)
    start = region.replay_start - timedelta(hours=12)
    end = region.replay_end

    def product_text(pid: str) -> str:
        p = text_dir / f"{pid}.txt"
        if not p.exists():
            r = client.get(f"{iem}/api/1/nwstext/{pid}")
            r.raise_for_status()
            p.write_text(r.text)
        return p.read_text()

    # 1) Storm-based warning polygons (each row is one polygon version).
    polygons = []
    for wfo in region.replay_wfos:
        r = client.get(f"{iem}/geojson/sbw.geojson",
                       params={"wfo": wfo, "sts": start.strftime("%Y-%m-%dT%H:%MZ"),
                               "ets": end.strftime("%Y-%m-%dT%H:%MZ")})
        r.raise_for_status()
        for f in r.json()["features"]:
            p = f["properties"]
            vkey = f"{p['phenomena']}.{p['significance']}.{int(p['eventid']):04d}"
            text = product_text(p["product_id"])
            polygons.append({
                "kind": "polygon",
                "id": f"{wfo}-{vkey}-{p['product_id']}",
                "event_key": f"{wfo}-{vkey}",
                "event": p["ps"],
                "phenomena": p["phenomena"],
                "significance": p["significance"],
                "eventid": p["eventid"],
                "status": p["status"],
                "issue": p["issue"],
                "expire": p["expire_utc"],
                "polygon_begin": p["polygon_begin"],
                "polygon_end": p["polygon_end"],
                "product_id": p["product_id"],
                "is_emergency": bool(p.get("is_emergency")),
                "is_pds": bool(p.get("is_pds")),
                "geometry": f["geometry"],
                **_split_alert_text(text, vkey),
            })
    log.info("Archived warning polygons: %d", len(polygons))

    # 2) Every VTEC event touching the region's counties/zones, sampled at the
    #    replay step so "in effect at t" is what the archive reported at t
    #    (including extensions and cancellations of polygon warnings).
    region_ugcs = _region_ugcs(region, client)
    zone_events: dict[str, dict] = {}
    samples: list[dict] = []
    t = start.replace(minute=0, second=0, microsecond=0)
    while t <= end:
        r = client.get(f"{iem}/api/1/vtec/county_zone.json", params={"valid": t.strftime("%Y-%m-%dT%H:%MZ")})
        r.raise_for_status()
        rows = r.json()
        rows = rows.get("data", rows) if isinstance(rows, dict) else rows
        active = set()
        for row in rows:
            if row.get("ugc") not in region_ugcs or row.get("wfo") not in region.replay_wfos:
                continue
            key = f"{row['wfo']}-{row['phenomena']}.{row['significance']}.{int(row['eventid']):04d}"
            active.add(key)  # polygon events too: the samples say when each was in effect
            if (row["phenomena"], row["significance"]) in POLYGON_TYPES:
                continue  # geometry and text come from the polygon archive
            ev = zone_events.setdefault(key, {
                "kind": "zone", "id": key, "event": row["event_label"], "phenomena": row["phenomena"],
                "significance": row["significance"], "eventid": row["eventid"], "wfo": row["wfo"],
                "issue": row["utc_issue"], "product_issue": row["utc_product_issue"], "ugcs": set(),
            })
            ev["ugcs"].add(row["ugc"])
        samples.append({"t": _iso(t), "active": sorted(active)})
        t += timedelta(minutes=region.replay_step_minutes)

    for key, ev in zone_events.items():
        ev["ugcs"] = sorted(ev["ugcs"])
        year = _parse_dt(ev["issue"]).year
        r = client.get(f"{iem}/json/vtec_event.py", params={
            "wfo": ev["wfo"], "year": year, "phenomena": ev["phenomena"],
            "significance": ev["significance"], "etn": ev["eventid"]})
        text = ""
        if r.status_code == 200:
            text = ((r.json().get("report") or {}).get("text")) or ""
        vkey = f"{ev['phenomena']}.{ev['significance']}.{int(ev['eventid']):04d}"
        ev.update(_split_alert_text(text, vkey))
        ev["geometry"] = _zones_geometry(ev["ugcs"], client)
    log.info("Archived zone events: %s", sorted(e["event"] for e in zone_events.values()))

    path.write_text(json.dumps({"polygons": polygons, "zone_events": list(zone_events.values()),
                                "zone_samples": samples}, default=list))


POLYGON_TYPES = {("FF", "W"), ("FA", "W"), ("FA", "Y"), ("FL", "W"), ("FL", "Y"), ("SV", "W"),
                 ("TO", "W"), ("MA", "W"), ("EW", "W"), ("SQ", "W"), ("DS", "W")}


def _region_ugcs(region: RegionConfig, client: httpx.Client) -> set[str]:
    w, s, e, n = region.bbox
    pts = [((s + n) / 2, (w + e) / 2), (s, w), (s, e), (n, w), (n, e)]
    ugcs: set[str] = set()
    for lat, lon in pts:
        r = client.get(f"{settings().nws_base}/zones", params={"point": f"{lat:.4f},{lon:.4f}"})
        if r.status_code != 200:
            continue
        for f in r.json().get("features", []):
            if f["properties"]["type"] in ("county", "public"):
                ugcs.add(f["properties"]["id"])
    return ugcs


def _zones_geometry(ugcs: list[str], client: httpx.Client) -> dict | None:
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    geoms = []
    for ugc in ugcs:
        kind = "county" if ugc[2] == "C" else "forecast"
        r = client.get(f"{settings().nws_base}/zones/{kind}/{ugc}")
        if r.status_code == 200 and r.json().get("geometry"):
            geoms.append(shape(r.json()["geometry"]))
    if not geoms:
        return None
    return mapping(unary_union(geoms).simplify(0.002))


def fetch_nhc(region: RegionConfig, client: httpx.Client, force: bool = False) -> None:
    path = replay_dir(region) / "nhc.json"
    if path.exists() and not force or not region.replay_nhc_storm:
        return
    import geopandas as gpd

    storm = region.replay_nhc_storm
    raw_dir = replay_dir(region) / "nhc_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    advisories = []
    misses = 0
    for n in range(1, 60):
        zpath = raw_dir / f"{storm}_5day_{n:03d}.zip"
        if not zpath.exists():
            r = client.get(f"{settings().nhc_gis_archive}/{storm}_5day_{n:03d}.zip")
            if r.status_code != 200:
                misses += 1
                if misses >= 3:
                    break
                continue
            zpath.write_bytes(r.content)
        misses = 0
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
        cone_shp = next((x for x in names if x.endswith("_pgn.shp")), None)
        pts_shp = next((x for x in names if x.endswith("_pts.shp")), None)
        if not cone_shp or not pts_shp:
            continue
        cone = gpd.read_file(f"zip://{zpath}!{cone_shp}").to_crs("EPSG:4326")
        pts = gpd.read_file(f"zip://{zpath}!{pts_shp}").to_crs("EPSG:4326")
        issued = _nhc_issue_time(cone, pts)
        if issued is None:
            continue
        track = []
        for _, p in pts.iterrows():
            vt = _nhc_valid_time(p, issued)
            if vt is None:
                continue
            track.append({"t": _iso(vt), "lat": p.geometry.y, "lon": p.geometry.x,
                          "max_wind_kt": float(p.get("MAXWIND") or 0), "label": p.get("DVLBL")})
        advisories.append({
            "advisory": n, "issued": _iso(issued),
            "storm_name": str(cone.iloc[0].get("STORMNAME") or "").title(),
            "cone": json.loads(cone.geometry.to_json())["features"][0]["geometry"],
            "track": track,
        })
    path.write_text(json.dumps(advisories))
    log.info("NHC: %d advisories", len(advisories))


def _nhc_issue_time(cone, pts) -> datetime | None:
    for col in ("ADVDATE",):
        for df in (pts, cone):
            if col in df.columns:
                v = str(df.iloc[0][col])
                m = re.match(r"^(\d{1,4}) (AM|PM) (\w{3}) \w{3} (\w{3}) (\d{1,2}) (\d{4})$", v)
                if m:
                    hhmm, ampm, tz, mon, day, year = m.groups()
                    hhmm = hhmm.zfill(4) if len(hhmm) > 2 else f"{int(hhmm):02d}00"
                    h, mi = int(hhmm[:-2]), int(hhmm[-2:])
                    h = h % 12 + (12 if ampm == "PM" else 0)
                    offs = {"EDT": -4, "EST": -5, "CDT": -5, "CST": -6, "AST": -4, "UTC": 0, "GMT": 0}.get(tz, 0)
                    local = datetime.strptime(f"{year} {mon} {day} {h} {mi}", "%Y %b %d %H %M")
                    return (local - timedelta(hours=offs)).replace(tzinfo=timezone.utc)
    return None


def _nhc_valid_time(p, issued: datetime) -> datetime | None:
    tau = p.get("TAU")
    if tau is not None:
        try:
            # TAU is hours from the synoptic time (advisory - 3 h).
            synoptic = issued.replace(minute=0) - timedelta(hours=issued.hour % 6)
            return synoptic + timedelta(hours=float(tau))
        except (TypeError, ValueError):
            return None
    return None


# ---------------------------------------------------------------------------
# Query layer
# ---------------------------------------------------------------------------


@dataclass
class Series:
    times: list[datetime]
    values: list[float]

    def at(self, t: datetime) -> tuple[datetime, float] | None:
        i = bisect.bisect_right(self.times, t) - 1
        if i < 0:
            return None
        return self.times[i], self.values[i]

    def until(self, t: datetime, since: datetime) -> list[tuple[datetime, float]]:
        lo = bisect.bisect_left(self.times, since)
        hi = bisect.bisect_right(self.times, t)
        return list(zip(self.times[lo:hi], self.values[lo:hi]))


class ReplayData:
    def __init__(self, region: RegionConfig):
        self.region = region
        d = replay_dir(region)
        self.series: dict[str, Series] = {}
        for g in region.gauges:
            p = d / "usgs" / f"{g.usgs_id}.json"
            if not g.usgs_id or not p.exists():
                continue
            pts = [(_parse_dt(t), v) for t, v in json.loads(p.read_text())["points"]]
            if g.replay_last_reading:
                pts = [x for x in pts if x[0] <= g.replay_last_reading]
            self.series[g.lid] = Series([x[0] for x in pts], [x[1] for x in pts])

        self.forecasts: dict[str, list[dict]] = {}
        p = d / "rvf_forecasts.json"
        if p.exists():
            for fc in json.loads(p.read_text()):
                fc = {**fc, "issued": _parse_dt(fc["issued"]),
                      "points": [(_parse_dt(t), v) for t, v in fc["points"]]}
                self.forecasts.setdefault(fc["lid"], []).append(fc)
            for v in self.forecasts.values():
                v.sort(key=lambda f: f["issued"])

        self.polygons: list[dict] = []
        self.zone_events: dict[str, dict] = {}
        self.zone_samples: list[tuple[datetime, list[str]]] = []
        p = d / "alerts.json"
        if p.exists():
            raw = json.loads(p.read_text())
            for a in raw["polygons"]:
                for k in ("issue", "expire", "polygon_begin", "polygon_end"):
                    a[k] = _parse_dt(a[k]) if a.get(k) else None
                # Product issuance time is the leading timestamp of the product id.
                a["product_issued"] = datetime.strptime(a["product_id"][:12], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
                self.polygons.append(a)
            self.polygons.sort(key=lambda a: a["product_issued"])
            for ev in raw["zone_events"]:
                ev["issue"] = _parse_dt(ev["issue"])
                ev["product_issue"] = _parse_dt(ev["product_issue"]) if ev.get("product_issue") else ev["issue"]
                self.zone_events[ev["id"]] = ev
            self.zone_samples = [(_parse_dt(s["t"]), s["active"]) for s in raw["zone_samples"]]

        self.nhc: list[dict] = []
        p = d / "nhc.json"
        if p.exists():
            for adv in json.loads(p.read_text()):
                adv["issued"] = _parse_dt(adv["issued"])
                for pt in adv["track"]:
                    pt["t"] = _parse_dt(pt["t"])
                self.nhc.append(adv)
            self.nhc.sort(key=lambda a: a["issued"])

    @property
    def available(self) -> bool:
        return bool(self.series)

    def reading_at(self, lid: str, t: datetime) -> tuple[datetime, float] | None:
        s = self.series.get(lid)
        return s.at(t) if s else None

    def observed_until(self, lid: str, t: datetime, hours: float = 48) -> list[tuple[datetime, float]]:
        s = self.series.get(lid)
        return s.until(t, t - timedelta(hours=hours)) if s else []

    def forecast_at(self, lid: str, t: datetime) -> dict | None:
        """Latest official forecast issued at or before t (no leakage)."""
        fcs = self.forecasts.get(lid) or []
        issued = [f["issued"] for f in fcs]
        i = bisect.bisect_right(issued, t) - 1
        if i < 0:
            return None
        fc = fcs[i]
        if not fc["points"] or fc["points"][-1][0] < t:
            return None
        return fc

    def alerts_at(self, t: datetime) -> list[dict]:
        """Alerts in effect at t, as the archive knew them at t.

        Whether an event is in effect comes from the hourly VTEC samples (they track
        extensions and cancellations); polygon geometry and text come from the
        event's first product, which must have been issued by t.
        """
        if not self.zone_samples:
            return []
        times = [s[0] for s in self.zone_samples]
        i = bisect.bisect_right(times, t) - 1
        if i < 0:
            return []
        active = set(self.zone_samples[i][1])
        out = []
        seen: set[str] = set()
        for a in self.polygons:
            key = a.get("event_key")
            if key not in active or key in seen:
                continue
            if a["product_issued"] > t or (a["issue"] and a["issue"] > t):
                continue
            seen.add(key)
            out.append(a)
        for key in active:
            ev = self.zone_events.get(key)
            if ev and ev["issue"] <= t and ev["product_issue"] <= t:
                out.append(ev)
        return out

    def nhc_at(self, t: datetime) -> dict | None:
        cands = [a for a in self.nhc if a["issued"] <= t]
        if not cands:
            return None
        adv = cands[-1]
        if t - adv["issued"] > timedelta(hours=12):
            return None
        return adv

    def window(self) -> tuple[datetime, datetime, int]:
        r = self.region
        return r.replay_start, r.replay_end, r.replay_step_minutes
