from __future__ import annotations

import os

os.environ.setdefault("WARM_REPLAY_CACHE", "false")
os.environ.setdefault("DATA_MODE", "replay")

from datetime import datetime, timedelta, timezone

import pytest

from app.config import region
from app.engine.decision import DecisionInput
from app.engine.triggers import TriggerResult
from app.models import (
    Destination, HazardZone, LatLon, LocationRisk, Metric, Profile, Route, RouteResult, Source, TimeLayer,
)

T0 = datetime(2024, 9, 27, 12, 0, tzinfo=timezone.utc)


def data_ready() -> bool:
    d = region().data_dir
    return (d / "derived" / "road_index.npz").exists() and (d / "replay" / "rvf_forecasts.json").exists()


needs_data = pytest.mark.skipif(not data_ready(), reason="run `python -m app.prepare` first")


def mk_route(mode: str = "walk", arrive_min: float = 30, kind: str = "refuge", name: str = "Community Center",
             accessible: bool | None = None, pet: bool | None = None) -> Route:
    dest = Destination(id=f"d-{name}", name=name, kind=kind, label="Higher ground", lat=35.6, lon=-82.55,
                       accessible=accessible, pet_friendly=pet, source=Source.OSM)
    return Route(mode=mode, destination=dest, geometry={"type": "LineString", "coordinates": [[-82.56, 35.58], [-82.55, 35.6]]},
                 edge_ids=["1-2-0"], distance_m=1200, duration_s=arrive_min * 60, depart_at=T0,
                 arrive_at=T0 + timedelta(minutes=arrive_min), deadline=None, slack_min=None, steps=[],
                 speed_mps=1.3 if mode == "walk" else None, google_maps_url=None, apple_maps_url=None)


def mk_zone(event: str, warning: bool = True) -> HazardZone:
    return HazardZone(id=f"z-{event}", hazard="nws_alert", geometry={"type": "Point", "coordinates": [0, 0]},
                      severity=3 if warning else 2, layer=TimeLayer.NOW, valid_from=T0, valid_to=T0 + timedelta(hours=1),
                      source=Source.NWS, reason="", event=event, is_warning=warning)


def mk_metric(key: str, value: float, category: str, severity: int, source=Source.AIRNOW) -> Metric:
    return Metric(key=key, label=key, value=value, unit="", category=category, severity=severity, advice="",
                  source=source, layer=TimeLayer.NOW, observed_or_valid_at=T0)


def flood_risk(hours: float | None = 3, ground: float = 600.0, fmax: float = 602.0) -> LocationRisk:
    return LocationRisk(ground_elev_m=ground, in_corridor=True, reach="french_broad", distance_to_river_m=80,
                        water_elev_now_m=598.0, water_elev_forecast_max_m=fmax,
                        t_flood_here=None if hours is None else T0 + timedelta(hours=hours),
                        flooded_now=False, cut_off_now=False)


def mk_input(*, flood: bool = False, risk: LocationRisk | None = None, alerts=None, metrics=None,
             profile: Profile | None = None, checkin=None, route_fn=None, hazard: str | None = None) -> DecisionInput:
    trig = TriggerResult(crisis=bool(flood or alerts or hazard), hazard=hazard or ("flood" if flood else None),
                         reasons=["test"] if (flood or alerts or hazard) else [], flood=flood)
    calls: list[str] = []

    def default_route(kind, eff):
        calls.append(kind)
        return RouteResult(route=mk_route(mode="drive" if eff.has_vehicle else "walk"), backup=None, t=T0)

    inp = DecisionInput(t=T0, tz="America/New_York", profile=profile or Profile(), checkin=checkin, trig=trig,
                        risk=risk, alerts_here=alerts or [], metrics={m.key: m for m in (metrics or [])},
                        route_fn=route_fn or default_route)
    inp.calls = calls  # type: ignore[attr-defined]
    return inp


@pytest.fixture
def here() -> LatLon:
    return LatLon(lat=35.5865, lon=-82.5683)


@pytest.fixture(scope="session")
def haven():
    from app.state import Haven, set_haven
    h = Haven(mode="replay")
    set_haven(h)
    return h


@pytest.fixture(scope="session")
def client(haven):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
