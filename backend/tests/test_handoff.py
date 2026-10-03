"""Google / Apple Maps handoff (spec §7.7 step 7, scenario 11)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from shapely.geometry import Point

from app.geo.routing import apple_maps_url, divergence_waypoints, google_maps_url

# A straight east-west street of nodes 0..40, ~100 m apart; node i at lon = i * 0.0011.
XY = {i: (-82.60 + i * 0.0011, 35.58) for i in range(41)}
# Detour nodes (north of the street) used by Haven's route.
for i in range(100, 200):
    XY[i] = (-82.60 + (i - 100) * 0.0011, 35.5815)


def coords_for(nodes):
    return [list(XY[n]) for n in nodes]


def test_identical_routes_no_waypoints():
    nodes = list(range(0, 41))
    url, wps = google_maps_url((35.58, -82.60), (35.58, -82.556), coords_for(nodes), nodes, nodes, [], "walk",
                               node_xy=XY.__getitem__)
    assert wps == []
    assert "waypoints" not in parse_qs(urlparse(url).query)
    assert "travelmode=walking" in url


def test_five_divergences_keep_three_nearest_hazards():
    fastest = list(range(0, 41))
    haven = []
    detours = [(3, 5), (10, 12), (17, 19), (24, 26), (31, 33)]
    i = 0
    for a, b in detours:
        haven += list(range(i, a + 1))
        haven += [100 + a + 1]
        i = b
    haven += list(range(i, 41))
    # Hazards near detours 2, 4 and 5 (by index): the URL must keep exactly those.
    hazards = [Point(XY[11][0], XY[11][1] - 0.0003), Point(XY[25][0], XY[25][1] - 0.0003),
               Point(XY[32][0], XY[32][1] - 0.0003)]
    url, wps = google_maps_url((35.58, -82.60), (35.58, -82.556), coords_for(haven), haven, fastest, hazards,
                               "drive", node_xy=XY.__getitem__)
    q = parse_qs(urlparse(url).query)
    got = q["waypoints"][0].split("|")
    assert len(got) == 3
    lons = [float(w.split(",")[1]) for w in got]
    assert lons == sorted(lons)  # route order
    for lon, want in zip(lons, (10, 24, 31)):
        assert abs(lon - XY[want][0]) < 0.0015
    assert q["travelmode"] == ["driving"]


def test_divergence_waypoints_capped_without_hazards():
    fastest = list(range(0, 41))
    haven = [0, 101, 2, 3, 104, 5, 6, 107, 8, 9, 110, 11, 12] + list(range(13, 41))
    wps = divergence_waypoints(coords_for(haven), haven, fastest, [], XY.__getitem__)
    assert len(wps) == 3


def test_apple_maps_url():
    assert apple_maps_url((35.5, -82.5), (35.6, -82.4), "walk").endswith("dirflg=w")
    assert "saddr=35.500000,-82.500000" in apple_maps_url((35.5, -82.5), (35.6, -82.4), "drive")
