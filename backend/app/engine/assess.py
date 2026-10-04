"""Wires world state -> metrics -> triggers -> decision for /api/assess."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shapely.geometry import Point

from app.engine.decision import DecisionInput, Effective, decide, questions_for, simulated_wording
from app.engine.triggers import evaluate_triggers, location_risk
from app.geo.road_impact import AT_RISK
from app.geo.routing import RouteRequest
from app.models import AssessRequest, AssessResponse, LatLon, Metric, RouteResult

if TYPE_CHECKING:
    from app.state import Haven, World

log = logging.getLogger(__name__)
AVOID_RADIUS_M = 1500.0


def metrics_at(ctx: "Haven", p: LatLon, t) -> list[Metric]:
    out: list[Metric] = []
    for name in ("alerts", "river_flood", "air_quality", "heat", "uv", "wildfire", "earthquake", "hurricane"):
        try:
            out.extend(ctx.modules[name].metrics_at(p, t))
        except Exception as ex:  # noqa: BLE001 - one source failing must not break the page
            log.warning("metrics from %s failed: %s", name, ex)
    return out


def avoid_roads(ctx: "Haven", w: "World", p: LatLon) -> list[str]:
    x, y = ctx.impact.to_work.transform(p.lon, p.lat)
    pt = Point(x, y)
    tree = ctx.impact.edge_tree()
    geoms = ctx.impact._edge_geoms()
    hits = []
    for j in tree.query(pt, predicate="dwithin", distance=AVOID_RADIUS_M):
        i, g = geoms[j]
        e = ctx.impact.edges[i]
        if not e["name"]:
            continue
        if w.impact.status_now[i] >= AT_RISK or w.impact.first_flood[i] is not None:
            hits.append((g.distance(pt), e["name"]))
    seen, out = set(), []
    for _, n in sorted(hits):
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out[:5]


def assess(ctx: "Haven", req: AssessRequest) -> AssessResponse:
    t = ctx.resolve_t(req.t)
    w = ctx.world(t)
    p = req.location
    metrics = metrics_at(ctx, p, t)
    mdict = {m.key: m for m in metrics}
    alerts_here = ctx.modules["alerts"].at_point(p, t)
    risk = location_risk(ctx, w, p)
    trig = evaluate_triggers(ctx, w, p, req.profile, risk, alerts_here, mdict)
    near_fire = ctx.modules["wildfire"].nearest(p)

    def route_fn(kind: str, eff: Effective) -> RouteResult | None:
        mode = "drive" if eff.has_vehicle else "walk"
        rr = RouteRequest(
            lat=p.lat, lon=p.lon, mode=mode, t=t, companions=eff.companions, needs_help=eff.needs_help,
            blocked=req.blocked, deadline=risk.t_flood_here if kind == "flood" else None,
            include_high_ground=kind == "flood", purpose="center" if kind in ("clean_air", "cooling") else kind,
        )
        try:
            return ctx.router.route(rr, w)
        except Exception as ex:  # noqa: BLE001
            log.exception("routing failed: %s", ex)
            return None

    inp = DecisionInput(
        t=t, tz=ctx.region.timezone, profile=req.profile, checkin=req.checkin, trig=trig, risk=risk,
        alerts_here=alerts_here, metrics=mdict, route_fn=route_fn,
        avoid_roads=avoid_roads(ctx, w, p) if trig.flood else [],
        fire_km=near_fire[1] if near_fire else None, replay=ctx.mode == "replay",
        simulated=ctx.mode == "replay" and ctx.region.replay_simulated, overlay=ctx.overlay,
        emergency_management=ctx.region.emergency_management,
    )
    verdict = decide(inp)
    if ctx.settings.gemini_api_key:
        from app.engine.rephrase import rephrase
        verdict.rephrased = rephrase(verdict)
    hazard = "flood" if trig.flood else trig.hazard
    return AssessResponse(
        crisis=trig.crisis, hazard=hazard,
        triggers=[simulated_wording(r) for r in trig.reasons] if inp.simulated else trig.reasons, verdict=verdict,
        questions=questions_for(hazard if trig.crisis else None, req.checkin), risk=risk, t=t, data_mode=ctx.mode,
    )
