"""Rule-based decision engine (spec §9; the flood flow of §7.7 is authoritative
for floods). Ordered, transparent, first matching rule wins. Official NWS alert
text is attached to every verdict and shown above it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from app.config import (
    CHECKIN_TTL_H, FLOOR_HEIGHT_M, LEAVE_WINDOW_H, RIVER_SEVERITY, ROUTE_BUFFER_MIN, SHELTER_WINDOW_H,
    UPPER_FLOOR_MARGIN_M, FIRE_LEAVE_KM,
)
from app.engine import steps as S
from app.engine.triggers import FIRE_EVAC_EVENTS, TriggerResult
from app.models import (
    CheckIn, CheckInOption, CheckInQuestion, Companions, GaugeStatus, HazardZone, LocationRisk, Metric, Profile,
    Route, RouteResult, Source, Verdict,
)

LABELS = {
    5: ("🚨", "Leave now / get to higher ground"),
    4: ("🏛️", "Go to a shelter / center"),
    3: ("🛡️", "Shelter in place"),
    2: ("🏠", "Stay indoors"),
    1: ("⚠️", "Limit time outdoors"),
    0: ("✅", "Safe outdoors"),
}


@dataclass
class Effective:
    floor: int
    stories: int
    has_vehicle: bool
    companions: Companions
    water_entering: bool
    needs_help: bool
    place: str | None
    power_on: bool | None
    assumptions: list[dict[str, str]] = field(default_factory=list)


def effective_checkin(profile: Profile, ci: CheckIn | None, t: datetime, hazard: str | None) -> Effective:
    """Fill unanswered check-in questions with the cautious default (spec §7.7 step 3)."""
    if ci and ci.answered_at and t - _utc(ci.answered_at) > timedelta(hours=CHECKIN_TTL_H) and t > _utc(ci.answered_at):
        ci = None  # expired
    ci = ci or CheckIn()
    flood = hazard == "flood"
    a: list[dict[str, str]] = []
    floor = ci.floor if ci.floor is not None else 0
    stories = ci.building_stories if ci.building_stories is not None else max(1, floor + 1)
    if ci.floor is None and ci.building_stories is None and flood:
        a.append({"field": "floor", "text": "Assuming you're on the ground floor of a one-story building. Tap to change."})
    elif ci.building_stories is None and flood:
        a.append({"field": "building_stories", "text": f"Assuming your building has {stories} floor(s). Tap to change."})
    has_vehicle = ci.has_vehicle_now if ci.has_vehicle_now is not None else False
    if ci.has_vehicle_now is None and flood:
        a.append({"field": "has_vehicle_now", "text": "Assuming no car available. Tap to change."})
    companions = ci.companions if ci.companions is not None else Companions(count=1, limited_mobility=True)
    if ci.companions is None and flood:
        a.append({"field": "companions",
                  "text": "Assuming one person with limited mobility is with you (slower walking pace). Tap to change."})
    return Effective(floor=floor, stories=stories, has_vehicle=has_vehicle, companions=companions,
                     water_entering=bool(ci.water_entering), needs_help=bool(ci.needs_help), place=ci.place,
                     power_on=ci.power_on, assumptions=a)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class DecisionInput:
    t: datetime
    tz: str
    profile: Profile
    checkin: CheckIn | None
    trig: TriggerResult
    risk: LocationRisk | None
    alerts_here: list[HazardZone]
    metrics: dict[str, Metric]
    route_fn: Callable[[str, Effective], RouteResult | None]
    avoid_roads: list[str] = field(default_factory=list)
    fire_km: float | None = None
    replay: bool = False
    overlay: bool = True  # terrain + road data available for this location


def _fmt(dt: datetime, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%a %-I:%M %p")


def timeline(inp: DecisionInput) -> list[str]:
    out = []
    tz = inp.tz
    for z in inp.alerts_here:
        if z.is_warning:
            out.append(f"{z.event} in effect" + (f" until {_fmt(z.valid_to, tz)}" if z.valid_to else "") + " (NWS)")
    for g in inp.trig.near_gauges[:3]:
        short = g.short_name
        if g.observed_stage_ft is not None:
            out.append(f"{short}: {g.observed_stage_ft:.1f} ft now ({g.category_now if g.category_now != 'none' else 'below flood stage'})"
                       + (" — gauge offline" if not g.online else ""))
        if g.forecast:
            for cat in ("minor", "moderate", "major"):
                when = g.time_to_category.get(cat)
                if when and when > inp.t:
                    out.append(f"{cat.title()} flood stage at {short} ~{_fmt(when, tz)} (NOAA forecast)")
            if g.forecast_peak_ft is not None and g.forecast_peak_at and g.forecast_peak_at > inp.t:
                out.append(f"Crest {g.forecast_peak_ft:.1f} ft at {short} ~{_fmt(g.forecast_peak_at, tz)} (NOAA forecast)")
        elif g.note:
            out.append(f"{short}: {g.note}")
    r = inp.risk
    if r and r.t_flood_here:
        if r.flooded_now:
            out.insert(0, "Your location is below the current water level estimate (gauge level + terrain)")
        elif r.cut_off_now:
            out.insert(0, "Every road within 300 m of you is flooded now (gauge level + terrain)")
        else:
            out.insert(0, f"Your location or every road out of it expected to flood ~{_fmt(r.t_flood_here, tz)} "
                          "(NOAA forecast + terrain)")
    return out[:7]


def _verdict(level: int, reason: str, inp: DecisionInput, steps: list[str], eff: Effective,
             route: Route | None = None, backup: Route | None = None, fallback: str | None = None,
             label: str | None = None, sources: set[Source] | None = None) -> Verdict:
    emoji, default_label = LABELS[level]
    src = set(sources or set())
    if inp.alerts_here:
        src.add(Source.NWS)
    if inp.trig.near_gauges:
        src.add(Source.USGS if inp.replay else Source.NWPS)
    if route:
        src.add(Source.OSM)
        if route.destination.source == Source.FEMA:
            src.add(Source.FEMA)
    return Verdict(level=level, label=label or default_label, emoji=emoji, reason=reason, timeline=timeline(inp),
                   steps=steps, route=route, backup_route=backup, fallback=fallback, assumptions=eff.assumptions,
                   sources=sorted(src, key=lambda s: s.value), alerts=[z for z in inp.alerts_here])


def decide(inp: DecisionInput) -> Verdict:
    t = inp.t
    hazard = inp.trig.hazard
    eff = effective_checkin(inp.profile, inp.checkin, t, "flood" if inp.trig.flood else hazard)
    events = [(z.event or "").lower() for z in inp.alerts_here if z.is_warning]
    risk = inp.risk
    p = inp.profile

    def has_event(*words: str) -> bool:
        return any(any(w in e for w in words) for e in events)

    gauge_src = "NOAA river forecast" if any(g.forecast for g in inp.trig.near_gauges) else "river gauge readings"

    # -- Flood: water already entering (spec §7.7 step 6, row 1) -----------------
    if eff.water_entering:
        return _verdict(3, "Water is entering or surrounding your building.", inp,
                        S.flood_shelter(True, eff.companions, p, no_route=True), eff,
                        label="Shelter in place: go to the highest floor now", sources={Source.HAVEN})

    # -- Tornado warning ------------------------------------------------------------
    if has_event("tornado"):
        home = p.home_type if (eff.place in (None, "home")) else None
        return _verdict(3, "A Tornado Warning covers your location (NWS).", inp, S.tornado(home, eff.place), eff)

    # -- Fire: detection within 5 km and an official fire / evacuation alert --------------
    if inp.fire_km is not None and inp.fire_km <= FIRE_LEAVE_KM and has_event(*FIRE_EVAC_EVENTS):
        rr = inp.route_fn("fire", eff)
        route = rr.route if rr else None
        return _verdict(5, f"Satellite fire detection {inp.fire_km:.1f} km away and an official fire/evacuation alert.",
                        inp, S.fire_leave(route), eff, route=route, backup=rr.backup if rr else None,
                        sources={Source.FIRMS})

    # -- Flood flow (spec §7.7) -------------------------------------------------------------
    if inp.trig.flood and risk is not None:
        t_here = risk.t_flood_here
        location_floods = (risk.water_elev_forecast_max_m is not None and risk.ground_elev_m is not None
                           and risk.water_elev_forecast_max_m > risk.ground_elev_m)
        highest = max(eff.stories - 1, eff.floor)
        refuge = (risk.ground_elev_m or 0) + FLOOR_HEIGHT_M * highest
        upper_valid = (not location_floods) or (
            risk.water_elev_forecast_max_m is not None and refuge > risk.water_elev_forecast_max_m + UPPER_FLOOR_MARGIN_M)
        upper_known = not any(a["field"] in ("floor", "building_stories") for a in eff.assumptions)
        upper_text = (f"go to floor {highest} (about {refuge - (risk.water_elev_forecast_max_m or refuge):.1f} m above the forecast peak water level)"
                      if location_floods and highest > 0 else "stay where you are")

        if t_here is not None and t_here - t <= timedelta(hours=SHELTER_WINDOW_H):
            within6 = t_here - t <= timedelta(hours=LEAVE_WINDOW_H)
            rr = inp.route_fn("flood", eff)
            route = rr.route if rr else None
            deadline = t_here - timedelta(minutes=ROUTE_BUFFER_MIN)
            in_time = (route is not None and route.arrive_at <= deadline
                       and (route.slack_min is None or route.slack_min >= 0))
            if t_here <= t:
                why = "Your location or every road out of it is flooded now, based on current river gauge readings and terrain."
            else:
                why = (f"Your location or every road out of it is expected to be flooded around {_fmt(t_here, inp.tz)}, "
                       f"based on the {gauge_src} and terrain.")
            if in_time:
                level = 5 if within6 else 4
                fb = f"If you can't leave: {upper_text}." if upper_valid and location_floods and highest > 0 else None
                st = S.flood_leave(route, inp.avoid_roads, eff.companions, p) if level == 5 else \
                    S.flood_go_to_center(route, inp.avoid_roads, eff.companions, p)
                return _verdict(level, why + f" The least risky route arrives by {_fmt(route.arrive_at, inp.tz)}.",
                                inp, st, eff, route=route, backup=rr.backup, fallback=fb, sources={Source.HAVEN})
            if upper_valid:
                on_upper = location_floods and highest > 0
                label = "Shelter in place on the upper floor" if on_upper else "Shelter in place"
                reason = why + " No route arrives in time, but " + (
                    "your upper floor is above the forecast peak water level." if on_upper
                    else "your building itself is not forecast to flood.")
                return _verdict(3, reason, inp, S.flood_shelter(True, eff.companions, p, no_route=route is None), eff,
                                label=label, sources={Source.HAVEN},
                                fallback=None if upper_known else "Tell us your floor so we can check whether it stays dry.")
            reason = why + " No open route found in this data that arrives in time."
            st = S.flood_shelter(False, eff.companions, p, no_route=True)
            st.insert(1, "Call 911 and share your location with an emergency contact.")
            return _verdict(3, reason, inp, st, eff, label="Shelter in place at the highest point available",
                            sources={Source.HAVEN},
                            fallback="Official help: Buncombe County Emergency Management, or call 911.")

    # -- Severe thunderstorm / extreme wind warning --------------------------------------------
    if has_event("severe thunderstorm", "extreme wind"):
        return _verdict(3, "A Severe Thunderstorm or Extreme Wind Warning covers your location (NWS).", inp,
                        S.severe_storm(), eff)

    # -- Go to a shelter / center ---------------------------------------------------------------
    aqi, heat = inp.metrics.get("aqi"), inp.metrics.get("heat_index")
    sens = 1 if p.sensitive_health else 0
    aqi_sev = min(4, aqi.severity + sens) if aqi and aqi.available and aqi.value is not None else 0
    heat_sev = min(4, heat.severity + sens) if heat and heat.available and heat.value is not None else 0
    no_purifier = p.has_purifier is not True
    no_ac = p.has_ac is not True or eff.power_on is False
    if aqi_sev >= 3 and no_purifier:
        rr = inp.route_fn("clean_air", eff)
        if p.has_purifier is None:
            eff.assumptions.append({"field": "has_purifier", "text": "Assuming no air purifier at home. Tap to change."})
        fc = inp.metrics.get("aqi_forecast")
        return _verdict(4, f"Air quality is {aqi.category} (AQI {aqi.value:.0f}) and you may not be able to clean your indoor air.",
                        inp, S.clean_air_center(rr.route if rr else None), eff, route=rr.route if rr else None,
                        backup=rr.backup if rr else None, sources={aqi.source} | ({fc.source} if fc else set()))
    if heat_sev >= 3 and no_ac:
        rr = inp.route_fn("cooling", eff)
        if p.has_ac is None:
            eff.assumptions.append({"field": "has_ac", "text": "Assuming no air conditioning. Tap to change."})
        return _verdict(4, f"Heat index {heat.value:.0f}°F ({heat.category}) and you may not have working air conditioning.",
                        inp, S.heat_center(rr.route if rr else None), eff, route=rr.route if rr else None,
                        backup=rr.backup if rr else None, sources={Source.NWS})

    # -- Flood: not reaching you (stay put and monitor) ------------------------------------------
    if inp.trig.flood and not inp.overlay:
        return _verdict(2, "Official river gauges near you show flooding or forecast it. Haven has no terrain or "
                           "road data for this area, so it can't tell whether the water reaches you: follow official "
                           "warnings and stay away from low ground near streams.", inp,
                        S.flood_monitor(eff.companions, p), eff, label="Stay alert and avoid low areas")
    if inp.trig.flood:
        when = risk.t_flood_here if risk else None
        reason = ("Flooding is happening or forecast nearby, but official forecasts don't show it reaching "
                  "your location or cutting off every road out" + ("" if when is None else
                  f" within 12 hours (expected ~{_fmt(when, inp.tz)})") + ".")
        return _verdict(2, reason, inp, S.flood_monitor(eff.companions, p), eff, label="Stay put and monitor",
                        sources={Source.HAVEN})

    # -- Stay indoors -------------------------------------------------------------------------------
    if aqi_sev >= 2:
        fc = inp.metrics.get("aqi_forecast")
        return _verdict(2, f"Air quality is {aqi.category} (AQI {aqi.value:.0f}).", inp,
                        S.smoke_indoors(fc.category if fc else None, p.has_purifier), eff, sources={aqi.source})
    if heat_sev >= 3:
        return _verdict(2, f"Heat index {heat.value:.0f}°F ({heat.category}).", inp, S.heat_indoors(), eff,
                        sources={Source.NWS})
    if has_event("winter storm", "ice storm", "blizzard"):
        return _verdict(2, "A winter storm warning covers your location (NWS).", inp, S.winter_indoors(), eff)
    if has_event("tropical storm", "hurricane"):
        return _verdict(2, "A Tropical Storm or Hurricane Warning covers your location (NWS).", inp,
                        S.hurricane_indoors(), eff)
    if inp.trig.crisis and hazard == "hurricane":
        return _verdict(2, inp.trig.reasons[0], inp, S.hurricane_indoors(), eff, sources={Source.NHC})
    if has_event("warning"):
        return _verdict(2, "An official warning covers your location (NWS).", inp,
                        ["Follow the official instructions above.", "Charge your phone and check back for updates."], eff)

    # -- Limit time outdoors ---------------------------------------------------------------------------
    watches = sorted({z.event for z in inp.alerts_here if not z.is_warning and z.event})
    sev1 = [m for m in inp.metrics.values() if m.available and m.severity >= 1 and m.key != "alerts"]
    if sev1 or watches:
        bits = ([f"{', '.join(watches)} in effect (NWS)"] if watches else []) + [f"{m.label}: {m.category}" for m in sev1]
        return _verdict(1, "; ".join(bits) + ".", inp, S.caution(watches), eff,
                        sources={m.source for m in sev1})
    return _verdict(0, "No official alerts, and current conditions are within normal ranges.", inp,
                    ["Enjoy your day. Haven keeps watching official sources."], eff)


# ---------------------------------------------------------------------------
# Check-in questions (spec §7.7 step 3, §12)
# ---------------------------------------------------------------------------

YES_NO = [CheckInOption(label="Yes", value=True), CheckInOption(label="No", value=False)]


def questions_for(hazard: str | None, ci: CheckIn | None) -> list[CheckInQuestion]:
    ci = ci or CheckIn()
    qs: list[CheckInQuestion] = []
    if hazard == "flood":
        if ci.floor is None or ci.building_stories is None:
            qs.append(CheckInQuestion(id="floor", field="floor", prompt="Which floor are you on?", options=[
                CheckInOption(label="Ground", value=0), CheckInOption(label="1st", value=1),
                CheckInOption(label="2nd", value=2), CheckInOption(label="3rd+", value=3)]))
            qs.append(CheckInQuestion(id="building_stories", field="building_stories",
                                      prompt="How many floors does the building have?", options=[
                CheckInOption(label="1", value=1), CheckInOption(label="2", value=2),
                CheckInOption(label="3", value=3), CheckInOption(label="4+", value=4)]))
        if ci.has_vehicle_now is None:
            qs.append(CheckInQuestion(id="has_vehicle_now", field="has_vehicle_now",
                                      prompt="Do you have access to a car right now?", options=YES_NO))
        if ci.companions is None:
            qs.append(CheckInQuestion(id="companions", field="companions", prompt="Who is with you?", multi=True, options=[
                CheckInOption(label="Just me", value="none"), CheckInOption(label="Kids", value="kids"),
                CheckInOption(label="Older adults", value="older_adults"),
                CheckInOption(label="Someone with limited mobility", value="limited_mobility"),
                CheckInOption(label="Pets", value="pets")]))
        if ci.water_entering is None:
            qs.append(CheckInQuestion(id="water_entering", field="water_entering",
                                      prompt="Is water entering or surrounding your building?", options=YES_NO))
        if ci.needs_help is None:
            qs.append(CheckInQuestion(id="needs_help", field="needs_help",
                                      prompt="Does anyone with you need medical help now?", options=YES_NO))
    elif hazard in ("tornado", "severe_storm"):
        if ci.place is None:
            qs.append(CheckInQuestion(id="place", field="place", prompt="Where are you right now?", options=[
                CheckInOption(label="Home", value="home"), CheckInOption(label="Work", value="work"),
                CheckInOption(label="In a car", value="car"), CheckInOption(label="Outside", value="outside"),
                CheckInOption(label="Other building", value="other")]))
    elif hazard in ("heat", "smoke"):
        if ci.power_on is None:
            qs.append(CheckInQuestion(id="power_on", field="power_on", prompt="Is your power on?", options=YES_NO))
        if ci.has_vehicle_now is None:
            qs.append(CheckInQuestion(id="has_vehicle_now", field="has_vehicle_now",
                                      prompt="Do you have access to a car right now?", options=YES_NO))
    return qs


def gauge_category_rank(g: GaugeStatus) -> int:
    return RIVER_SEVERITY[g.category_now]
