"""Road impact sanity (M3) and routing (M4) on the real Helene data."""

from __future__ import annotations

import collections
from datetime import datetime, timezone

import numpy as np
import pytest

from app.geo.road_impact import FLOODED
from app.geo.routing import RouteRequest
from app.models import Companions

from tests.conftest import needs_data

PEAK = datetime(2024, 9, 27, 21, 45, tzinfo=timezone.utc)
BASE = datetime(2024, 9, 25, 4, 0, tzinfo=timezone.utc)


def flooded_names(h, t, graph="drive"):
    w = h.world(h.resolve_t(t))
    c = collections.Counter()
    for e in h.impact.edges:
        if e["graph"] == graph and w.impact.status_now[e["i"]] == FLOODED and e["name"]:
            c[e["name"]] += 1
    return w, c


@needs_data
def test_m3_lowlands_flood_at_peak(haven):
    _, names = flooded_names(haven, PEAK)
    # River Arts District and Biltmore Village streets that flooded during Helene.
    for street in ("Riverside Drive", "Lyman Street", "Amboy Road", "Meadow Road", "All Souls Crescent"):
        assert names[street] > 0, street


@needs_data
def test_m3_baseline_dry(haven):
    w, names = flooded_names(haven, BASE)
    assert sum(names.values()) == 0


@needs_data
def test_m3_hillsides_stay_dry(haven):
    """No flooded sample may sit far above the highest gauge water surface: that
    would indicate a datum or unit bug."""
    w = haven.world(haven.resolve_t(PEAK))
    lv = w.impact.levels.now
    top = np.nanmax(lv)
    flooded = {i for i in range(haven.impact.E) if w.impact.status_now[i] == FLOODED}
    ri = haven.impact
    for k, e in enumerate(ri.sampled_edges):
        if e in flooded:
            lo = ri.starts[k]
            hi = ri.starts[k + 1] if k + 1 < len(ri.starts) else len(ri.s_z)
            assert ri.s_z[lo:hi].min() < top, "flooded edge entirely above the highest water surface"


@needs_data
def test_m3_bridges_not_falsely_flooded(haven):
    """At baseline flow every bridge is dry even though the DEM under it is the riverbed."""
    w = haven.world(haven.resolve_t(BASE))
    bridges = [e["i"] for e in haven.impact.edges if e["bridge"] and e["crosses_river"]]
    assert bridges, "expected river bridges in the corridor"
    assert all(w.impact.status_now[i] == 0 for i in bridges)


@needs_data
def test_m4_routes_change_over_time(haven):
    # Biltmore Village: before the storm the nearest refuge is the Cathedral of All
    # Souls; once the official forecast puts it under the crest, the route changes.
    lat, lon = 35.5675, -82.5440
    out = []
    for t in (datetime(2024, 9, 25, 12, tzinfo=timezone.utc), datetime(2024, 9, 27, 10, tzinfo=timezone.utc)):
        tt = haven.resolve_t(t)
        rr = haven.router.route(RouteRequest(lat=lat, lon=lon, mode="drive", t=tt,
                                             companions=Companions(count=0)), haven.world(tt))
        out.append(rr)
    a, b = out
    assert a.route and b.route
    assert a.route.destination.name == "Cathedral of All Souls"
    assert b.route.destination.id != a.route.destination.id


@needs_data
def test_m4_no_route_case_handled(haven):
    t = haven.resolve_t(PEAK)
    rr = haven.router.route(RouteRequest(lat=35.5865, lon=-82.5683, mode="walk", t=t), haven.world(t))
    assert rr.route is None
    assert rr.message == "No open route found in this data."


@needs_data
def test_route_avoids_flooded_and_forecast_flooded_edges(haven):
    t = haven.resolve_t(datetime(2024, 9, 27, 3, tzinfo=timezone.utc))
    w = haven.world(t)
    rr = haven.router.route(RouteRequest(lat=35.5865, lon=-82.5683, mode="walk", t=t,
                                         companions=Companions(count=0)), w)
    assert rr.route is not None
    idx = {e["eid"]: e["i"] for e in haven.impact.edges if e["graph"] == "walk"}
    for eid in rr.route.edge_ids:
        i = idx.get(eid)
        if i is None:
            continue
        assert w.impact.status_now[i] == 0
        ff = w.impact.first_flood[i]
        assert ff is None or ff > rr.route.arrive_at


@needs_data
def test_assess_end_to_end(client):
    body = {"location": {"lat": 35.5865, "lon": -82.5683}, "t": "2024-09-27T03:00:00Z",
            "checkin": {"has_vehicle_now": False, "floor": 0, "building_stories": 1,
                        "companions": {"count": 0}, "water_entering": False}}
    js = client.post("/api/assess", json=body).json()
    assert js["crisis"] is True and js["hazard"] == "flood"
    v = js["verdict"]
    assert v["level"] == 5
    assert v["route"]["label"] == "Least risky route in this data"
    assert v["route"]["mode"] == "walk"


@needs_data
def test_base_flow_nothing_flooded_any_graph(haven):
    """At base flow (pre-storm stage) no road or path is flooded or at risk."""
    import numpy as np
    from app.config import FT_PER_M
    from app.geo.road_impact import GaugeLevels
    lv = np.array([(haven.gauge_meta[g.lid]["datum_navd88_ft"] + g.base_flow_stage_ft) / FT_PER_M
                   for g in haven.region.gauges])
    res = haven.impact.evaluate(BASE, GaugeLevels(now=lv, fc_times=[], fc=np.zeros((3, 0)),
                                                  stale=np.zeros(3, bool)), with_extent=False)
    assert int((res.status_now > 0).sum()) == 0


@needs_data
def test_replan_check_flags_route_when_data_changes(client, haven):
    """Spec §7.7 step 8: the re-plan check passes for a fresh route and flags
    edges that the newer data shows flooded."""
    rr = client.get("/api/route", params={"lat": 35.5865, "lon": -82.5683, "mode": "walk",
                                          "t": "2024-09-26T20:00:00Z", "companions": "none"}).json()
    route = rr["route"]
    assert route is not None
    same = client.post("/api/route/check", json={"mode": "walk", "edge_ids": route["edge_ids"],
                                                  "arrive_at": route["arrive_at"], "t": "2024-09-26T20:00:00Z"}).json()
    assert same["ok"] is True
    # An edge that is dry Thursday afternoon (and not forecast to flood before
    # arrival) but flooded Friday afternoon must be flagged by the later check.
    thu = haven.world(haven.resolve_t(datetime(2024, 9, 26, 20, tzinfo=timezone.utc)))
    fri = haven.world(haven.resolve_t(datetime(2024, 9, 27, 18, tzinfo=timezone.utc)))
    eid = next(e["eid"] for e in haven.impact.edges
               if e["graph"] == "walk" and thu.impact.status_now[e["i"]] == 0 and thu.impact.first_flood[e["i"]] is None
               and fri.impact.status_now[e["i"]] == FLOODED)
    ok_then = client.post("/api/route/check", json={"mode": "walk", "edge_ids": [eid],
                                                     "arrive_at": "2024-09-26T20:30:00Z", "t": "2024-09-26T20:00:00Z"}).json()
    later = client.post("/api/route/check", json={"mode": "walk", "edge_ids": [eid],
                                                   "arrive_at": "2024-09-27T18:30:00Z", "t": "2024-09-27T18:00:00Z"}).json()
    assert ok_then["ok"] is True
    assert later["ok"] is False and later["problems"][0]["status"] == "flooded"
