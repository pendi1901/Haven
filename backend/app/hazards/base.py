"""Hazard module interface (spec §6) and shared helpers."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol

from app.models import HazardZone, LatLon, Metric, Source, TimeLayer

if TYPE_CHECKING:
    from app.state import Haven


class HazardModule(Protocol):
    name: str

    async def refresh(self, t: datetime) -> None: ...

    def zones(self, t: datetime) -> list[HazardZone]: ...

    def metrics_at(self, p: LatLon, t: datetime) -> list[Metric]: ...


class BaseModule:
    name = "base"

    def __init__(self, ctx: "Haven"):
        self.ctx = ctx

    async def refresh(self, t: datetime) -> None:  # live pollers call refresh
        return None

    def zones(self, t: datetime) -> list[HazardZone]:
        return []

    def metrics_at(self, p: LatLon, t: datetime) -> list[Metric]:
        return []

    # -- helpers ------------------------------------------------------------

    @property
    def replay(self) -> bool:
        return self.ctx.mode == "replay"

    def unavailable(self, key: str, label: str, unit: str, source: Source, why: str) -> Metric:
        return Metric(key=key, label=label, value=None, unit=unit, category="Unavailable", severity=0,
                      advice=why, source=source, layer=TimeLayer.NOW, observed_or_valid_at=None,
                      available=False)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def compass(deg: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((deg % 360) / 45 + 0.5) % 8]


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
