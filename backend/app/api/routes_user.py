"""GET /api/metrics and POST /api/assess (spec §10). Profiles and check-ins are
used for the request only and never stored server-side (spec §14)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.api.routes_state import _parse_t
from app.engine.assess import assess, metrics_at
from app.models import AssessRequest, AssessResponse, LatLon, Metric
from app.state import get_haven

router = APIRouter(prefix="/api")


def _check_bounds(ctx, lat: float, lon: float) -> None:
    w, s, e, n = ctx.region.bbox
    if not (s - 0.2 <= lat <= n + 0.2 and w - 0.2 <= lon <= e + 0.2):
        raise HTTPException(422, f"Location is outside the {ctx.region.name} region Haven has data for.")


@router.get("/metrics", response_model=list[Metric])
def get_metrics(lat: float, lon: float, t: str | None = Query(None)):
    ctx = get_haven()
    _check_bounds(ctx, lat, lon)
    return metrics_at(ctx, LatLon(lat=lat, lon=lon), ctx.resolve_t(_parse_t(t)))


@router.post("/assess", response_model=AssessResponse)
async def post_assess(req: AssessRequest):
    ctx = get_haven()
    _check_bounds(ctx, req.location.lat, req.location.lon)
    return await run_in_threadpool(assess, ctx, req)
