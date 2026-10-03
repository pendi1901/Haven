"""Decision engine scenario tests (spec §13, scenarios 1-10)."""

from __future__ import annotations

from datetime import timedelta

from app.engine.decision import decide
from app.geo.routing import Candidate, Router, RouteRequest, walk_speed
from app.models import CheckIn, Companions, Destination, LatLon, Profile, RouteResult, Source

from tests.conftest import T0, flood_risk, mk_input, mk_metric, mk_route, mk_zone


def test_1_tornado_warning_shelter_in_place():
    v = decide(mk_input(alerts=[mk_zone("Tornado Warning")]))
    assert v.level == 3
    assert any("interior room" in s for s in v.steps)


def test_1b_tornado_mobile_home():
    v = decide(mk_input(alerts=[mk_zone("Tornado Warning")], profile=Profile(home_type="mobile_home")))
    assert v.level == 3
    assert any("sturdy building" in s for s in v.steps)


def test_2_aqi_sensitive_no_purifier_goes_to_center():
    inp = mk_input(metrics=[mk_metric("aqi", 210, "Very Unhealthy", 3)],
                   profile=Profile(sensitive_health=True, has_purifier=False), hazard="smoke")
    v = decide(inp)
    assert v.level == 4
    assert inp.calls == ["clean_air"]
    assert v.route is not None


def test_3_aqi_not_sensitive_with_purifier_stays_indoors():
    v = decide(mk_input(metrics=[mk_metric("aqi", 210, "Very Unhealthy", 3)],
                        profile=Profile(sensitive_health=False, has_purifier=True), hazard="smoke"))
    assert v.level == 2
    assert v.label == "Stay indoors"


def test_4_flood_in_3h_route_exists_walk():
    v = decide(mk_input(flood=True, risk=flood_risk(3), checkin=CheckIn(has_vehicle_now=False)))
    assert v.level == 5
    assert v.route is not None and v.route.mode == "walk"
    assert "Least risky route in this data" == v.route.label


def test_5_flood_all_routes_cut():
    inp = mk_input(flood=True, risk=flood_risk(3), checkin=CheckIn(has_vehicle_now=False),
                   route_fn=lambda kind, eff: RouteResult(route=None, backup=None, t=T0,
                                                          message="No open route found in this data."))
    v = decide(inp)
    assert v.level == 3
    assert "No open route found in this data" in v.reason
    assert v.route is None


def test_6_water_entering_overrides_route():
    v = decide(mk_input(flood=True, risk=flood_risk(3), checkin=CheckIn(water_entering=True, has_vehicle_now=True)))
    assert v.level == 3
    assert v.route is None
    assert "highest floor" in v.label


def test_7_no_hazards_safe():
    v = decide(mk_input(metrics=[mk_metric("aqi", 20, "Good", 0), mk_metric("uv", 2, "Low", 0, Source.EPA)]))
    assert v.level == 0


def test_8_upper_floor_when_no_route_in_time():
    # Route arrives after the deadline; 3rd floor of a 4-story building:
    # refuge = 600 + 3 m x 3 = 609 m > forecast peak 602 m + 1 m.
    inp = mk_input(flood=True, risk=flood_risk(3, ground=600, fmax=602),
                   checkin=CheckIn(floor=3, building_stories=4, has_vehicle_now=False),
                   route_fn=lambda kind, eff: RouteResult(route=mk_route(arrive_min=200), backup=None, t=T0))
    v = decide(inp)
    assert v.level == 3
    assert v.label == "Shelter in place on the upper floor"


def test_8b_upper_floor_too_low_falls_to_highest_point():
    inp = mk_input(flood=True, risk=flood_risk(3, ground=600, fmax=605),
                   checkin=CheckIn(floor=1, building_stories=2, has_vehicle_now=False),
                   route_fn=lambda kind, eff: RouteResult(route=None, backup=None, t=T0))
    v = decide(inp)
    assert v.level == 3
    assert v.label == "Shelter in place at the highest point available"


def test_9_limited_mobility_speed_and_deadline():
    assert walk_speed(Companions(limited_mobility=True)) == 0.8
    assert walk_speed(Companions(kids=True)) == 1.0
    assert walk_speed(Companions()) == 1.3
    assert walk_speed(None) == 0.8  # unanswered: cautious default

    # ETA at 0.8 m/s misses the deadline -> falls back to case 8 (upper floor) or 5.
    late = lambda kind, eff: RouteResult(route=mk_route(arrive_min=170), backup=None, t=T0)  # noqa: E731
    v = decide(mk_input(flood=True, risk=flood_risk(3), route_fn=late,
                        checkin=CheckIn(companions=Companions(count=1, limited_mobility=True), floor=0,
                                        building_stories=1, has_vehicle_now=False)))
    assert v.level == 3 and "No open route" in v.reason


def _cand(name, cost, accessible=None, pet=None, kind="refuge", priority=0):
    d = Destination(id=name, name=name, kind=kind, label="x", lat=35.6, lon=-82.5, accessible=accessible,
                    pet_friendly=pet, source=Source.OSM)
    return Candidate(dest=d, node=hash(name) % 10000, cost=cost, priority=priority)


def test_9b_only_accessible_destinations_for_limited_mobility():
    r = Router.__new__(Router)
    req = RouteRequest(lat=0, lon=0, mode="walk", t=T0, companions=Companions(count=1, limited_mobility=True))
    cands = [_cand("Church", 600), _cand("Library", 900, accessible=True), _cand("School", 700)]
    primary, _backup, _notes = r._choose(cands, req)
    assert primary.dest.name == "Library"


def test_10_pets_prefer_pet_friendly_within_20_percent():
    r = Router.__new__(Router)
    req = RouteRequest(lat=0, lon=0, mode="drive", t=T0, companions=Companions(count=1, pets=True))
    cands = [_cand("Nearest", 1000, kind="open_shelter"), _cand("PetOK", 1150, pet=True, kind="open_shelter")]
    assert r._choose(cands, req)[0].dest.name == "PetOK"
    cands = [_cand("Nearest", 1000, kind="open_shelter"), _cand("PetFar", 1300, pet=True, kind="open_shelter")]
    assert r._choose(cands, req)[0].dest.name == "Nearest"


def test_unanswered_checkin_lists_cautious_assumptions():
    v = decide(mk_input(flood=True, risk=flood_risk(3)))
    fields = {a["field"] for a in v.assumptions}
    assert {"floor", "has_vehicle_now", "companions"} <= fields
    assert v.route.mode == "walk"  # no car assumed


def test_flood_6_to_12h_goes_to_center():
    v = decide(mk_input(flood=True, risk=flood_risk(9), checkin=CheckIn(has_vehicle_now=True)))
    assert v.level == 4
    assert v.route.mode == "drive"


def test_flood_not_reaching_location_monitor():
    v = decide(mk_input(flood=True, risk=flood_risk(None)))
    assert v.level == 2
    assert v.label == "Stay put and monitor"


def test_watch_limits_outdoors():
    v = decide(mk_input(alerts=[mk_zone("Flood Watch", warning=False)]))
    assert v.level == 1
