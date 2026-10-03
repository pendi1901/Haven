"""GET /api/metrics and POST /api/assess (spec §10). Profiles and check-ins are
used for the request only and never stored server-side (spec §14)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

from app.api.routes_state import _parse_t
from app.engine.assess import assess, metrics_at
from app.models import AssessRequest, AssessResponse, LatLon, Metric
from app.api.deps import DataMode, context

router = APIRouter(prefix="/api")


@router.get("/metrics", response_model=list[Metric])
async def get_metrics(lat: float, lon: float, t: str | None = Query(None), data_mode: DataMode | None = None):
    ctx = await context(data_mode, lat, lon)
    return await run_in_threadpool(metrics_at, ctx, LatLon(lat=lat, lon=lon), ctx.resolve_t(_parse_t(t)))


@router.post("/assess", response_model=AssessResponse)
async def post_assess(req: AssessRequest):
    ctx = await context(req.data_mode, req.location.lat, req.location.lon)
    return await run_in_threadpool(assess, ctx, req)
