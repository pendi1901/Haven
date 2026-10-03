"""GET /api/responder (spec §10, §12 screen 5, M8).

Cut-off areas: census tracts whose road nodes have no drive path to any hospital
after removing flooded and closed roads (current observed data at t). Population
cut off is estimated as tract population x share of the tract's road nodes that
lost hospital access.

Accuracy (replay only): compares roads Haven flagged flooded (using the highest
observed gauge levels up to t, never later data) with NCDOT's post-storm closure
snapshot (Oct 8, 2024) filtered to the evaluated river corridor. Scoring only:
the snapshot never influences routing (spec §11).
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache

import networkx as nx
import numpy as np
import shapely
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

from app.api.routes_state import _parse_t
from app.config import CORRIDOR_M, FT_PER_M
from app.geo.census import closures_path, tracts_path
from app.geo.road_impact import CLOSED, FLOODED, GaugeLevels
from app.api.deps import DataMode, context
from app.state import Haven

router = APIRouter(prefix="/api")
MATCH_M = 30.0


@lru_cache(maxsize=1)
def _tracts(region_key: str, path: str) -> list[dict]:
    try:
        return json.loads(open(path).read())["features"]
    except OSError:
        return []


def _tract_nodes(ctx: Haven, tracts: list[dict]) -> list[np.ndarray]:
    gd = ctx.router.graph("drive")
    lon = np.array([gd.G.nodes[int(n)]["x"] for n in gd.nodes])
    lat = np.array([gd.G.nodes[int(n)]["y"] for n in gd.nodes])
    out = []
    for f in tracts:
        g = shape(f["geometry"])
        out.append(np.where(shapely.contains_xy(g, lon, lat))[0])
    return out


_tract_nodes_cache: dict[str, list[np.ndarray]] = {}


def responder(ctx: Haven, t: datetime) -> dict:
    w = ctx.world(t)
    gd = ctx.router.graph("drive")
    removed = {e["eid"] for e in ctx.impact.edges
               if e["graph"] == "drive" and w.impact.status_now[e["i"]] in (FLOODED, CLOSED)}
    hospitals = []
    hz = [shape(z.geometry) for z in w.zones if z.hazard == "river_flood" and z.layer.value == "now"]
    hz_union = shapely.union_all(hz) if hz else None
    for p in ctx.pois:
        if p["amenity"] != "hospital":
            continue
        from shapely.geometry import Point
        if hz_union is not None and hz_union.covers(Point(p["lon"], p["lat"])):
            continue
        n, d = ctx.router.nearest_node(gd, p["lat"], p["lon"])
        if d <= 300:
            hospitals.append((p, n))
    R = gd.G.reverse(copy=False)

    def reach(block: set[str]) -> set[int]:
        def weight(u, v, dd):
            best = None
            for x in dd.values():
                if x["eid"] in block:
                    continue
                c = x.get("travel_time") or 1.0
                best = c if best is None or c < best else best
            return best
        if not hospitals:
            return set()
        return set(nx.multi_source_dijkstra_path_length(R, {n for _, n in hospitals}, weight=weight).keys())

    base = _baseline_reach(ctx, tuple(sorted(n for _, n in hospitals)), reach)
    now = reach(removed)
    lost = base - now

    tracts = _tracts(ctx.region.key, str(tracts_path(ctx.region)))
    if ctx.region.key not in _tract_nodes_cache:
        _tract_nodes_cache[ctx.region.key] = _tract_nodes(ctx, tracts)
    tnodes = _tract_nodes_cache[ctx.region.key]
    node_ids = gd.nodes
    feats, ranked = [], []
    total_pop = 0.0
    for f, idx in zip(tracts, tnodes):
        ids = {int(node_ids[i]) for i in idx}
        base_n = len(ids & base)
        if base_n == 0:
            continue
        share = len(ids & lost) / base_n
        if share <= 0:
            continue
        p = f["properties"]
        pop = float(p.get("POP100") or 0)
        est = pop * share
        total_pop += est
        props = {"geoid": p["GEOID"], "name": p["NAME"], "share_cut_off": round(share, 3),
                 "population": int(pop), "population_cut_off_est": int(round(est))}
        if p.get("age65") is not None:
            props["age65_cut_off_est"] = int(round(p["age65"] * share))
            props["no_vehicle_households_cut_off_est"] = int(round(p["no_vehicle_households"] * share))
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": props})
        ranked.append(props)
    ranked.sort(key=lambda r: (-(r["population_cut_off_est"] + 2 * r.get("age65_cut_off_est", 0)
                                 + 3 * r.get("no_vehicle_households_cut_off_est", 0))))
    return {
        "t": t,
        "available": True,
        "hospitals": [{"name": p["name"], "lat": p["lat"], "lon": p["lon"]} for p, _ in hospitals],
        "cut_off_areas": {"type": "FeatureCollection", "features": feats},
        "population": int(round(total_pop)),
        "priority_list": ranked[:15],
        "acs": bool(tracts and tracts[0]["properties"].get("age65") is not None),
        "accuracy": accuracy(ctx, t) if ctx.mode == "replay" else None,
        "notes": ["Population: 2020 Census (POP100) x share of tract road nodes without a drive path to a hospital."]
                 + ([] if (tracts and tracts[0]["properties"].get("age65") is not None)
                    else ["Set CENSUS_API_KEY and rerun `python -m app.prepare --steps census --force census` "
                          "to add age 65+ and no-vehicle households (ACS)."]),
    }


_base_cache: dict = {}


def _baseline_reach(ctx: Haven, key: tuple, reach_fn) -> set[int]:
    if key not in _base_cache:
        _base_cache[key] = reach_fn(set())
    return _base_cache[key]


def accuracy(ctx: Haven, t: datetime) -> dict | None:
    path = closures_path(ctx.region)
    if not path.exists():
        return None
    feats = json.loads(path.read_text()).get("features", [])
    to_work = lambda g: shp_transform(lambda x, y, z=None: ctx.impact.to_work.transform(x, y), g)  # noqa: E731
    corridor = shapely.union_all([r.line for r in ctx.impact.full_reaches()]).buffer(CORRIDOR_M)
    closures = []
    for f in feats:
        p = f["properties"]
        if p.get("Condition") not in ("Road Closed", "Road Impassable"):
            continue
        g = to_work(shape(f["geometry"]))
        if g.intersects(corridor):
            closures.append((p.get("CommonName"), p.get("Reason"), g.intersection(corridor.buffer(50))))
    # Highest observed level per gauge up to t (no data after t).
    lids = ctx.impact.gauges
    peak = np.full(len(lids), np.nan)
    for i, lid in enumerate(lids):
        obs = ctx.replay.observed_until(lid, t, hours=24 * 30)
        meta = ctx.gauge_meta.get(lid) or {}
        if obs and meta.get("datum_navd88_ft") is not None:
            peak[i] = (meta["datum_navd88_ft"] + max(v for _, v in obs)) / FT_PER_M
    res = ctx.impact.evaluate(t, GaugeLevels(now=peak, fc_times=[], fc=np.zeros((len(lids), 0)),
                                             stale=np.zeros(len(lids), bool)), with_extent=False)
    geoms = ctx.impact._edge_geoms()
    flagged = [(ctx.impact.edges[i], geoms[i][1]) for i in range(ctx.impact.E)
               if ctx.impact.edges[i]["graph"] == "drive" and res.status_now[i] == FLOODED]
    if not closures:
        return {"closures_in_corridor": 0, "note": "No NCDOT closures intersect the evaluated corridor."}
    ftree = shapely.STRtree([g for _, g in flagged]) if flagged else None
    hit = 0
    detail = []
    for name, reason, g in closures:
        ok = bool(ftree is not None and len(ftree.query(g, predicate="dwithin", distance=MATCH_M)))
        hit += ok
        detail.append({"road": name, "reason": (reason or "").strip(), "matched": ok})
    ctree = shapely.STRtree([g for *_, g in closures])
    matched_edges = sum(1 for _, g in flagged if len(ctree.query(g, predicate="dwithin", distance=MATCH_M)))
    return {
        "as_of": t,
        "closures_in_corridor": len(closures),
        "recall": round(hit / len(closures), 3),
        "flagged_drive_edges": len(flagged),
        "precision_lower_bound": round(matched_edges / len(flagged), 3) if flagged else None,
        "closures": detail,
        "note": ("NCDOT snapshot from Oct 8, 2024 (post-storm), state-maintained roads only, filtered to the "
                 "evaluated river corridor. Many closures were landslides or washed-out bridges. City streets "
                 "Haven flags have no NCDOT counterpart, so precision is a lower bound. Scoring only; never used "
                 "for routing."),
    }


@router.get("/responder")
async def get_responder(t: str | None = None, data_mode: DataMode | None = None, lat: float | None = None,
                        lon: float | None = None):
    ctx = await context(data_mode, lat, lon)
    tt = ctx.resolve_t(_parse_t(t))
    if not ctx.overlay:
        return {"t": tt, "available": False, "hospitals": [], "cut_off_areas": {"type": "FeatureCollection", "features": []},
                "population": 0, "priority_list": [], "acs": False, "accuracy": None,
                "notes": [f"The responder view needs prepared road data; {ctx.region.name} doesn't have it yet."]}
    return await run_in_threadpool(responder, ctx, tt)
