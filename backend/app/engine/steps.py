"""Action step templates (spec §9), tailored to who is with the user."""

from __future__ import annotations

from app.models import Companions, Profile, Route

MOVING_WATER = ("Never walk or drive through moving water: six inches can knock a person down "
                "and about a foot can carry away a car.")
TURN_AROUND = "If water covers the road ahead, turn around and tap “I can't continue this way” to reroute."
PACK = "Pack medications, phone charger, ID and documents."
SHARE = "Share your location with an emergency contact."
CALL_911_RISING = "If water is rising inside, call 911."
ATTIC = "Do not climb into a closed attic: you can be trapped by rising water. Go onto the roof only if you must."


def household_steps(c: Companions | None, profile: Profile, leaving: bool) -> list[str]:
    out = []
    kids = (c and c.kids) or "kids" in profile.household
    older = (c and c.older_adults) or "older_adults" in profile.household
    limited = (c is None) or c.limited_mobility or "limited_mobility" in profile.household
    pets = (c and c.pets) or "pets" in profile.household
    if limited:
        out.append("If someone can't walk the route, call 911 now and tell them your location and floor."
                   if leaving else "If someone can't get upstairs, call 911 now and give your exact location.")
    if kids:
        out.append("Keep children within arm's reach; carry small kids near any water.")
    if older:
        out.append("Bring older adults' medications and mobility aids.")
    if pets:
        out.append("Bring pets on leashes or in carriers, with food and medication." if leaving
                   else "Bring pets to the highest floor with you.")
    return out


def flood_leave(route: Route, avoid: list[str], c: Companions | None, profile: Profile) -> list[str]:
    s = [f"Leave now. Follow the route to {route.destination.name} ({route.destination.label.lower()})."]
    if avoid:
        s.append("Avoid " + ", ".join(avoid[:3]) + ": flooded or forecast to flood.")
    s += [PACK, MOVING_WATER, TURN_AROUND]
    s += household_steps(c, profile, leaving=True)
    return s


def flood_shelter(upper_floor: bool, c: Companions | None, profile: Profile, no_route: bool) -> list[str]:
    s = ["Move to the highest floor now." if upper_floor else "Go to the highest point you can reach in your building now."]
    s += [SHARE, CALL_911_RISING, ATTIC]
    if no_route:
        s.append("Do not try to drive or walk out through flooded roads.")
    s += household_steps(c, profile, leaving=False)
    s.append("Keep your phone charged and listen for official updates.")
    return s


def flood_monitor(c: Companions | None, profile: Profile) -> list[str]:
    return [
        "Stay where you are and avoid roads near the river and creeks.",
        "If you are in a low spot near a creek, follow the official warning and move to higher ground.",
        MOVING_WATER,
        "Charge your phone and keep this page open for changes.",
    ]


def flood_go_to_center(route: Route, avoid: list[str], c: Companions | None, profile: Profile) -> list[str]:
    s = [f"Plan to leave soon for {route.destination.name}; roads near you are forecast to flood later."]
    if avoid:
        s.append("Avoid " + ", ".join(avoid[:3]) + ".")
    s += [PACK, MOVING_WATER]
    s += household_steps(c, profile, leaving=True)
    return s


def tornado(home_type: str | None, place: str | None) -> list[str]:
    if place in ("car", "outside"):
        return ["Get into the nearest sturdy building now.",
                "If there is none, lie flat in a low spot away from vehicles and cover your head.",
                "Do not shelter under an overpass."]
    if home_type == "mobile_home":
        return ["Leave now for a sturdy building if you can reach one before the storm.",
                "If you can't, lie flat in a low spot away from trees and cover your head."]
    return ["Go to an interior room on the lowest floor, away from windows.",
            "Get under something sturdy and cover your head and neck.",
            "Stay there until the warning ends."]


def severe_storm() -> list[str]:
    return ["Go indoors to a sturdy building and stay away from windows.",
            "Avoid driving: falling trees and power lines block roads.",
            "Stay inside until the warning ends."]


def smoke_indoors(forecast: str | None, purifier: bool | None) -> list[str]:
    s = ["Keep windows and doors closed.",
         "Run an air purifier if you have one." if purifier is not False else "Set your AC to recirculate if you have it.",
         "Avoid outdoor exercise until AQI drops" + (f" ({forecast})." if forecast else ".")]
    return s


def clean_air_center(route: Route | None) -> list[str]:
    s = []
    if route:
        s.append(f"Go to {route.destination.name} for cleaner indoor air; call ahead to check it is open.")
    s += ["Wear a well-fitted N95 mask outdoors if you have one.", "Keep windows and doors closed until you leave."]
    return s


def heat_center(route: Route | None) -> list[str]:
    s = []
    if route:
        s.append(f"Go to {route.destination.name}; check hours before you go.")
    s += ["Drink water regularly, avoid exertion.", "Check on older neighbors and never leave anyone in a parked car."]
    return s


def heat_indoors() -> list[str]:
    return ["Stay in air conditioning during the hottest hours.", "Drink water regularly, avoid exertion."]


def hurricane_indoors() -> list[str]:
    return ["Stay indoors and away from windows.", "Avoid travel: trees and power lines will block roads.",
            MOVING_WATER, "Charge phones and keep a flashlight ready for power outages."]


def winter_indoors() -> list[str]:
    return ["Stay off the roads.", "Keep warm in one room; never run a generator or grill indoors."]


def fire_leave(route: Route | None) -> list[str]:
    s = []
    if route:
        s.append(f"Leave now. Follow the route to {route.destination.name}.")
    s += ["Follow evacuation orders from officials immediately.", "Close windows and doors behind you; take medications and documents."]
    return s


def caution(watch_names: list[str]) -> list[str]:
    s = []
    if watch_names:
        s.append(f"{', '.join(watch_names)} in effect: know where you would go if a warning is issued.")
    s.append("Charge your phone and check back for updates.")
    return s
