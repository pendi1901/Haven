"""Resolve the context (replay, prepared live region, or live point region) for a request."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Literal
from urllib.parse import parse_qs

from fastapi import HTTPException

from app.sources.locate import OutsideCoverage
from app.state import Haven, get_hub

DataMode = Literal["live", "replay"]

# Which replay scenario a request is about (`?scenario=<region key>`), e.g. the Helene
# replay or the simulated Walnut Creek flood. Set per request by ScenarioMiddleware,
# so every endpoint (GET and POST) gets it without its own parameter.
scenario_var: ContextVar[str | None] = ContextVar("scenario", default=None)


class ScenarioMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            qs = parse_qs(scope.get("query_string", b"").decode())
            token = scenario_var.set((qs.get("scenario") or [None])[0] or None)
            try:
                return await self.app(scope, receive, send)
            finally:
                scenario_var.reset(token)
        return await self.app(scope, receive, send)


async def context(data_mode: DataMode | None, lat: float | None = None, lon: float | None = None) -> Haven:
    hub = get_hub()
    try:
        h = await hub.get(data_mode, lat, lon, scenario=scenario_var.get())
    except OutsideCoverage as ex:
        raise HTTPException(422, str(ex)) from ex
    except LookupError as ex:
        raise HTTPException(404, str(ex)) from ex
    if h.mode == "replay" and lat is not None and lon is not None:
        w, s, e, n = h.region.bbox
        if not (s - 0.2 <= lat <= n + 0.2 and w - 0.2 <= lon <= e + 0.2):
            raise HTTPException(422, f"The replay covers {h.region.name}; pick a place there.")
    return h
