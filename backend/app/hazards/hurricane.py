"""Hurricane: official NHC 5-day cone and forecast track (spec §7.4, trigger 5).

"Tropical-storm or hurricane conditions expected within 48 h" is evaluated from
the official forecast track: a forecast point within 48 h at tropical-storm
strength (>= 34 kt) within TS_RADIUS_KM of the user, while the user is inside the
cone. This is a transparent reading of the NHC product, not a wind model.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from shapely.geometry import Point, box, shape

from app.config import TRIGGER_NHC_HOURS
from app.hazards.base import BaseModule, haversine_km, parse_dt
from app.models import HazardZone, LatLon, Metric, Source, TimeLayer
from app.sources.nhc import fetch_storms

TS_KT = 34
REGION_MARGIN_DEG = 3.0  # ~300 km
TS_RADIUS_KM = 250


class Hurricane(BaseModule):
    name = "hurricane"

    async def refresh(self, t: datetime) -> None:
        if self.replay:
            return
        try:
            self.ctx.cache.put("nhc", await fetch_storms(self.ctx.client))
        except Exception as ex:  # noqa: BLE001
            self.ctx.cache.fail("nhc", f"{type(ex).__name__}: {ex}")

    def storms(self, t: datetime) -> list[dict]:
        if self.replay:
            adv = self.ctx.replay.nhc_at(t)
            if not adv:
                return []
            return [{"id": self.ctx.region.replay_nhc_storm, "name": adv["storm_name"], "issued": adv["issued"],
                     "advisory": adv["advisory"], "cone": adv["cone"], "track": adv["track"], "archived": True}]
        out = []
        for s in self.ctx.cache.get("nhc").value or []:
            out.append({**s, "issued": parse_dt(s.get("issued")),
                        "track": [{**p, "t": parse_dt(p["t"])} for p in s.get("track") or []]})
        return out

    def zones(self, t: datetime) -> list[HazardZone]:
        out = []
        near = box(*self.ctx.region.bbox).buffer(REGION_MARGIN_DEG)
        for s in self.storms(t):
            # Only storms whose official cone comes near the region (not, e.g.,
            # an Eastern Pacific storm during an Asheville session).
            if not s.get("cone") or not shape(s["cone"]).intersects(near):
                continue
            label = f"advisory {s['advisory']} " if s.get("advisory") else ""
            out.append(HazardZone(
                id=f"nhc:{s['id']}", hazard="hurricane", geometry=s["cone"], severity=2, layer=TimeLayer.FORECAST,
                valid_from=s["issued"] or t, valid_to=None, source=Source.NHC,
                reason=f"NHC 5-day forecast cone for {s['name']} ({label}issued {s['issued']:%b %-d %H:%M} UTC"
                       f"{', archived' if s.get('archived') else ''}). The cone shows where the center may go; "
                       "impacts extend well outside it.",
                event=f"{s['name']} forecast cone"))
        return out

    def conditions_expected(self, p: LatLon, t: datetime) -> tuple[dict, datetime] | None:
        pt = Point(p.lon, p.lat)
        for s in self.storms(t):
            if not s.get("cone") or not shape(s["cone"]).covers(pt):
                continue
            for tp in sorted(s["track"], key=lambda x: x["t"]):
                if t <= tp["t"] <= t + timedelta(hours=TRIGGER_NHC_HOURS) and tp["max_wind_kt"] >= TS_KT \
                        and haversine_km(p.lat, p.lon, tp["lat"], tp["lon"]) <= TS_RADIUS_KM:
                    return s, tp["t"]
        return None

    def metrics_at(self, p: LatLon, t: datetime) -> list[Metric]:
        storms = self.storms(t)
        if not storms:
            return []
        hit = self.conditions_expected(p, t)
        if hit:
            s, when = hit
            return [Metric(key="hurricane", label=f"{s['name']}", value=None, unit="", category="Inside forecast cone",
                           severity=2, advice="Tropical-storm-force winds possible within 48 h per the NHC track. "
                                              "Finish preparations; expect heavy rain and flooding inland.",
                           source=Source.NHC, layer=TimeLayer.FORECAST, observed_or_valid_at=when,
                           detail=f"NHC forecast issued {s['issued']:%b %-d %H:%M} UTC")]
        inside = [s for s in storms if s.get("cone") and shape(s["cone"]).covers(Point(p.lon, p.lat))]
        if inside:
            s = inside[0]
            return [Metric(key="hurricane", label=s["name"], value=None, unit="", category="Inside forecast cone",
                           severity=1, advice="You are inside the official forecast cone. Review your plan.",
                           source=Source.NHC, layer=TimeLayer.FORECAST, observed_or_valid_at=s["issued"])]
        return []
