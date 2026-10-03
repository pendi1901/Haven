"""Resolve the context (replay, prepared live region, or live point region) for a request."""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException

from app.sources.locate import OutsideCoverage
from app.state import Haven, get_hub

DataMode = Literal["live", "replay"]


async def context(data_mode: DataMode | None, lat: float | None = None, lon: float | None = None) -> Haven:
    hub = get_hub()
    try:
        h = await hub.get(data_mode, lat, lon)
    except OutsideCoverage as ex:
        raise HTTPException(422, str(ex)) from ex
    except LookupError as ex:
        raise HTTPException(404, str(ex)) from ex
    if h.mode == "replay" and lat is not None and lon is not None:
        w, s, e, n = h.region.bbox
        if not (s - 0.2 <= lat <= n + 0.2 and w - 0.2 <= lon <= e + 0.2):
            raise HTTPException(422, f"The replay covers {h.region.name}; pick a place there.")
    return h
