"""GET /api/state, /api/meta, /api/replay/meta (spec §10)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.engine.assess import metrics_at
from app.geo.road_impact import STATUS_NAMES
from app.geo.roads import WALK_ONLY_HIGHWAYS
from app.models import LatLon
from fastapi.concurrency import run_in_threadpool

from app.api.deps import DataMode, context
from app.config import REGIONS
from app.state import get_hub

router = APIRouter(prefix="/api")


def roads_geojson(ctx, w) -> dict:
    """Affected roads only (anything not plainly dry), deduplicated across
    directions and graphs. Unaffected roads come from the basemap."""
    imp = w.impact
    seen: set = set()
    feats = []
    for e in ctx.impact.edges:
        i = e["i"]
        st = int(imp.status_now[i])
        ff = imp.first_flood[i]
        if st == 0 and ff is None:
            continue
        if e["graph"] == "walk" and e["highway"] not in WALK_ONLY_HIGHWAYS:
            continue  # streets are drawn from the drive graph
        key = (frozenset((e["u"], e["v"])), len(e["coords"]))
        if key in seen:
            continue
        seen.add(key)
        depth = imp.depth_now_ft[i]
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[round(x, 5), round(y, 5)] for x, y in e["coords"]]},
            "properties": {
                "id": f"{e['graph']}:{e['eid']}", "name": e["name"], "status": STATUS_NAMES[st],
                "first_flood_time_forecast": ff.isoformat() if ff else None,
                "depth_ft": None if depth != depth else round(float(depth), 1),
                "bridge": e["bridge"], "path": e["graph"] == "walk", "stale": bool(imp.stale_edges[i]),
            },
        })
    return {"type": "FeatureCollection", "features": feats}


def _parse_t(t: str | None) -> datetime | None:
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError as ex:
        raise HTTPException(400, f"Invalid t: {t}") from ex


@router.get("/state")
async def get_state(t: str | None = Query(None, description="Replay time (ISO 8601); ignored in live mode"),
                    data_mode: DataMode | None = None, lat: float | None = None, lon: float | None = None):
    ctx = await context(data_mode, lat, lon)
    tt = ctx.resolve_t(_parse_t(t))
    return await run_in_threadpool(_state, ctx, tt)


def _state(ctx, tt) -> dict:
    w = ctx.world(tt)
    lat, lon = ctx.region.center
    return {
        "data_mode": ctx.mode,
        "t": tt,
        "gauges": w.gauges,
        "zones": w.zones,
        "roads": roads_geojson(ctx, w),
        "metrics_region": metrics_at(ctx, LatLon(lat=lat, lon=lon), tt),
        "storms": [{k: v for k, v in s.items() if k != "cone"} for s in ctx.modules["hurricane"].storms(tt)],
        "fires": ctx.modules["wildfire"].detections(),
        "shelters": w.shelters,
        "counts": {
            "flooded": int((w.impact.status_now == 2).sum()),
            "at_risk": int((w.impact.status_now == 1).sum()),
            "closed": int((w.impact.status_now == 3).sum()),
            "forecast_flooded": sum(1 for x in w.impact.first_flood if x is not None),
        },
        "sources": ctx.source_status(),
        "version": ctx.version,
    }


@router.get("/meta")
async def get_meta(data_mode: DataMode | None = None, lat: float | None = None, lon: float | None = None):
    ctx = await context(data_mode, lat, lon)
    hub = get_hub()
    r = ctx.region
    return {
        "data_mode": ctx.mode,
        "modes": {"live": True, "replay": hub.replay_available()},
        "region": {"key": r.key, "name": r.name, "bbox": r.bbox, "center": r.center, "timezone": r.timezone,
                   "overlay": ctx.overlay, "point": r.point},
        "overlay_regions": [{"key": x.key, "name": x.name, "bbox": x.bbox} for x in REGIONS.values() if x.has_overlay],
        "demo_places": list(r.demo_places),
        "reaches": [{"id": x["id"], "river": x["river"], "upstream": x["upstream"], "downstream": x["downstream"],
                     "geometry": _reach_ll(ctx, x)} for x in ctx.impact.reaches_meta],
        "replay": _replay_meta(ctx) if ctx.mode == "replay" else None,
        "features": {"gemini": bool(ctx.settings.gemini_api_key), "handoff": ctx.mode == "live"},
        "dem_source": ctx.dem.source,
    }


def _reach_ll(ctx, x) -> dict:
    xs, ys = zip(*x["coords"])
    lo, la = ctx.impact.to_ll.transform(list(xs), list(ys))
    return {"type": "LineString", "coordinates": [[round(a, 5), round(b, 5)] for a, b in zip(lo, la)][::3]}


def _replay_meta(ctx) -> dict:
    start, end, step = ctx.replay.window()
    return {"start": start, "end": end, "step_minutes": step, "event": ctx.region.replay_event,
            "timezone": ctx.region.timezone}


@router.get("/replay/meta")
async def replay_meta():
    return _replay_meta(await context("replay"))
