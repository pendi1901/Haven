"""Simulated Walnut Creek flood (python -m app.replay.simulate): the demo story holds,
the scenario is selectable next to the Helene replay, and nothing in it claims to be
an official product."""

from __future__ import annotations

import json

import pytest

from app.config import REGIONS
from app.replay import simulate
from app.replay.loader import replay_dir

RALEIGH = REGIONS["raleigh"]

pytestmark = pytest.mark.skipif(not (RALEIGH.has_overlay and RALEIGH.has_replay),
                                reason="needs prepared Raleigh data and `python -m app.replay.simulate`")

PROFILE = {"home": None, "work": None, "home_type": None, "floor": None, "household": [], "sensitive_health": False,
           "usually_has_car": None, "has_ac": None, "has_purifier": None, "language": "en"}
SUNNYBROOK = (35.75822, -78.58282)
ROSE_LN = (35.76046, -78.60010)
TUE_2PM, TUE_9PM, TUE_11PM, WED_1AM = ("2026-09-29T18:00:00Z", "2026-09-30T01:00:00Z", "2026-09-30T03:00:00Z",
                                       "2026-09-30T05:00:00Z")


def assess(client, where, t):
    body = {"profile": PROFILE, "checkin": None, "location": {"lat": where[0], "lon": where[1]}, "t": t,
            "blocked": [], "data_mode": "replay"}
    r = client.post("/api/assess?scenario=raleigh", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_committed_files_match_the_generator():
    """The data in git is exactly what the generator writes (rerun it after editing)."""
    d = replay_dir(RALEIGH)
    assert json.loads((d / "rvf_forecasts.json").read_text()) == json.loads(json.dumps(simulate.forecasts(RALEIGH)))
    assert json.loads((d / "alerts.json").read_text()) == json.loads(json.dumps(simulate.alerts(RALEIGH)))
    series = simulate.gauge_series(RALEIGH)
    ids = {g.lid: g.usgs_id for g in RALEIGH.gauges}
    for lid, pts in series.items():
        on_disk = json.loads((d / "usgs" / f"{ids[lid]}.json").read_text())["points"]
        assert on_disk == [list(p) for p in pts]


def test_scenarios_listed_and_helene_stays_default(client):
    meta = client.get("/api/meta?data_mode=replay").json()
    assert meta["region"]["key"] == "asheville" and not meta["replay"]["simulated"]
    keys = {s["key"]: s for s in meta["scenarios"]}
    assert keys["raleigh"]["simulated"] and not keys["asheville"]["simulated"]
    sim = client.get("/api/meta?data_mode=replay&scenario=raleigh").json()
    assert sim["region"]["key"] == "raleigh" and sim["replay"]["simulated"]
    assert sim["replay"]["default_t"].startswith("2026-09-30T01:00")


def test_unknown_scenario_is_404(client):
    assert client.get("/api/meta?data_mode=replay&scenario=nowhere").status_code == 404


def test_storm_builds_then_recedes(client):
    def counts(t):
        return client.get(f"/api/state?data_mode=replay&scenario=raleigh&t={t}").json()["counts"]
    assert counts("2026-09-29T16:00:00Z")["flooded"] == 0
    assert counts(TUE_9PM)["forecast_flooded"] > 50
    assert counts("2026-09-30T09:00:00Z")["flooded"] > 50
    assert counts("2026-09-30T23:00:00Z")["flooded"] < counts("2026-09-30T09:00:00Z")["flooded"]


def test_everything_is_labeled_simulated(client):
    st = client.get(f"/api/state?data_mode=replay&scenario=raleigh&t={WED_1AM}").json()
    alerts = [z for z in st["zones"] if z["hazard"] == "nws_alert"]
    assert {z["event"] for z in alerts} == {"Flash Flood Warning", "Flood Warning", "Flood Watch"}
    for z in alerts:
        assert z["source"] == "simulated"
        assert z["headline"].startswith("Simulated") and "SIMULATED ALERT" in z["description"]
    assert all("Simulated" in g["forecast_source"] for g in st["gauges"] if g["forecast_source"])
    v = assess(client, SUNNYBROOK, TUE_9PM)
    text = " ".join([v["verdict"]["reason"], *v["verdict"]["timeline"], *v["triggers"]])
    assert "NOAA" not in text and "(NWS)" not in text
    assert "nws" not in v["verdict"]["sources"] and "simulated" in v["verdict"]["sources"]


def test_no_leakage_in_simulated_forecasts():
    for fc in simulate.forecasts(RALEIGH):
        assert all(t > fc["issued"] for t, _ in fc["points"])


def test_demo_story_sunnybrook(client):
    """2 PM watch only; 9 PM leave now on foot; 1 AM cut off, shelter in place (Wake County)."""
    assert assess(client, SUNNYBROOK, TUE_2PM)["verdict"]["level"] <= 1
    v = assess(client, SUNNYBROOK, TUE_9PM)["verdict"]
    assert v["level"] == 5 and v["route"] and v["route"]["mode"] == "walk"
    v = assess(client, SUNNYBROOK, WED_1AM)["verdict"]
    assert v["level"] == 3 and v["route"] is None and "Wake County" in v["fallback"]


def test_newer_forecast_upgrades_rose_lane(client):
    """At 9 PM the forecast keeps Rose Ln dry; the 11 PM forecast raises the crest."""
    assert assess(client, ROSE_LN, TUE_9PM)["verdict"]["level"] == 2
    assert assess(client, ROSE_LN, TUE_11PM)["verdict"]["level"] == 5


def test_replan_flags_rose_lane_route(client):
    """A walk planned at 11 PM is flagged once Rose Lane floods (the +1 h demo)."""
    lat, lon = ROSE_LN
    rt = client.get(f"/api/route?data_mode=replay&scenario=raleigh&lat={lat}&lon={lon}&mode=walk&t={TUE_11PM}").json()["route"]

    def check(t):
        return client.post("/api/route/check?scenario=raleigh", json={
            "mode": "walk", "edge_ids": rt["edge_ids"], "arrive_at": rt["arrive_at"], "t": t,
            "data_mode": "replay", "lat": lat, "lon": lon}).json()
    assert check(TUE_11PM)["ok"]
    later = check("2026-09-30T06:00:00Z")
    assert not later["ok"] and "Rose Lane" in [p["name"] for p in later["problems"]]


def test_hunt_library_stays_put(client):
    """NC State's Hunt Library (by Lake Raleigh, between the dams, not modeled): flooding
    nearby, but it never reaches the library or cuts it off."""
    for t in (TUE_9PM, WED_1AM):
        assert assess(client, (35.76935, -78.67638), t)["verdict"]["level"] == 2
