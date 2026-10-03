"""GET /api/route (spec §10)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.api.routes_state import _parse_t
from app.config import ROUTE_BUFFER_MIN
from app.engine.triggers import location_risk
from app.geo.road_impact import STATUS_NAMES
from app.geo.routing import RouteRequest
from app.models import Companions, LatLon, RouteResult
from app.api.deps import DataMode, context

router = APIRouter(prefix="/api")

FLAGS = {"kids", "older_adults", "limited_mobility", "pets"}


def parse_companions(companions: str | None, count: int | None) -> Companions | None:
    """`companions=kids,pets&count=2`; `companions=none` means just the user.
    Omitted means unknown, which routing treats with the cautious default."""
    if companions is None and count is None:
        return None
    flags = {f.strip() for f in (companions or "").split(",") if f.strip()}
    bad = flags - FLAGS - {"none"}
    if bad:
        raise HTTPException(422, f"Unknown companion flags: {sorted(bad)}")
    return Companions(count=count if count is not None else len(flags - {"none"}),
                      **{f: True for f in flags & FLAGS})


@router.get("/route", response_model=RouteResult)
async def get_route(
    lat: float, lon: float,
    mode: Literal["walk", "drive"] = "walk",
    t: str | None = None,
    companions: str | None = Query(None, description="Comma-separated: kids,older_adults,limited_mobility,pets or none"),
    count: int | None = Query(None, ge=0, le=50),
    blocked: str | None = Query(None, description="Comma-separated edge ids reported blocked"),
    needs_help: bool = False,
    purpose: Literal["flood", "center", "fire"] = "flood",
    data_mode: DataMode | None = None,
):
    ctx = await context(data_mode, lat, lon)
    tt = ctx.resolve_t(_parse_t(t))

    def run() -> RouteResult:
        w = ctx.world(tt)
        risk = location_risk(ctx, w, LatLon(lat=lat, lon=lon)) if purpose == "flood" else None
        req = RouteRequest(
            lat=lat, lon=lon, mode=mode, t=tt, companions=parse_companions(companions, count),
            needs_help=needs_help, blocked=[b for b in (blocked or "").split(",") if b],
            deadline=risk.t_flood_here if risk else None, include_high_ground=purpose == "flood", purpose=purpose,
        )
        return ctx.router.route(req, w)

    return await run_in_threadpool(run)


class RouteCheck(BaseModel):
    mode: Literal["walk", "drive"]
    edge_ids: list[str]
    arrive_at: datetime
    t: datetime | None = None
    data_mode: DataMode | None = None
    lat: float | None = None  # any point on the route (selects the region)
    lon: float | None = None


@router.post("/route/check")
async def check_route(body: RouteCheck):
    """Re-plan check (spec §7.7 step 8): is any edge of the active route now
    flooded / at risk / closed, or forecast to flood before arrival + buffer?"""
    ctx = await context(body.data_mode, body.lat, body.lon)
    tt = ctx.resolve_t(body.t)
    w = await run_in_threadpool(ctx.world, tt)
    limit = body.arrive_at + timedelta(minutes=ROUTE_BUFFER_MIN)
    problems = []
    for eid in body.edge_ids:
        i = ctx.impact.eid_index.get((body.mode, eid))
        if i is None:
            continue
        st = int(w.impact.status_now[i])
        ff = w.impact.first_flood[i]
        if st > 0 or (ff is not None and ff <= limit):
            problems.append({"edge_id": eid, "name": ctx.impact.edges[i]["name"], "status": STATUS_NAMES[st],
                             "first_flood": ff})
    return {"ok": not problems, "t": tt, "problems": problems}
