"""Replay data: SHEF parsing, no leakage, and milestone M1."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import region
from app.replay.loader import ReplayData
from app.replay.shef import parse_rvf

from tests.conftest import needs_data

RVF = """\
:FLETCHER, NC - FRENCH BROAD RIVER
.AR :CREST: FLCN7 0927 Z DC202409261903/DH1800/HGIFFXZ 30.5
.ER FLCN7 0927 Z DC202409261903/DH00/HGIFFZZ/DIH6
:RIVER FORECAST  / 12Z   / 18Z   /  00Z   / 06Z
.E1    : 0926 :                   /  18.1 /  23.9
.E2    : 0927 :   /  29.1 /  30.5 /  29.6 /  27.5
.ER FLCN7 0927 Z DC202409261903/DH00/PPQFZ/DIH6
.E1    : 0926 :                   /  1.67 /  1.88
"""


def test_shef_parses_stage_forecast_only():
    out = parse_rvf(RVF, {"FLCN7"})
    assert len(out) == 1
    fc = out[0]
    assert fc["issued"] == datetime(2024, 9, 26, 19, 3, tzinfo=timezone.utc)
    assert [v for _, v in fc["points"]] == [18.1, 23.9, 29.1, 30.5, 29.6, 27.5]
    assert fc["points"][0][0] == datetime(2024, 9, 27, 0, tzinfo=timezone.utc)
    assert fc["points"][3][0] == datetime(2024, 9, 27, 18, tzinfo=timezone.utc)


@needs_data
def test_no_leakage_forecasts_and_readings():
    rd = ReplayData(region())
    for t in (datetime(2024, 9, 26, 3, tzinfo=timezone.utc), datetime(2024, 9, 27, 15, 7, tzinfo=timezone.utc)):
        for lid in ("FLCN7", "AVLN7", "BLTN7"):
            fc = rd.forecast_at(lid, t)
            if fc:
                assert fc["issued"] <= t
            r = rd.reading_at(lid, t)
            if r:
                assert r[0] <= t
            assert all(pt <= t for pt, _ in rd.observed_until(lid, t))
        for a in rd.alerts_at(t):
            assert (a.get("polygon_begin") or a["issue"]) <= t
        adv = rd.nhc_at(t)
        assert adv is None or adv["issued"] <= t


@needs_data
def test_bltn7_goes_offline_after_last_reading():
    rd = ReplayData(region())
    late = datetime(2024, 9, 28, 12, tzinfo=timezone.utc)
    r = rd.reading_at("BLTN7", late)
    assert r is not None and r[0] <= datetime(2024, 9, 28, 2, 30, tzinfo=timezone.utc)
    assert late - r[0] > timedelta(hours=2)


@needs_data
def test_m1_state_returns_three_gauges(client):
    js = client.get("/api/state", params={"t": "2024-09-27T21:30:00Z"}).json()
    gauges = {g["lid"]: g for g in js["gauges"]}
    assert set(gauges) == {"FLCN7", "AVLN7", "BLTN7"}
    assert gauges["AVLN7"]["category_now"] == "major"
    assert gauges["AVLN7"]["observed_stage_ft"] > 24
    late = client.get("/api/state", params={"t": "2024-09-28T12:00:00Z"}).json()
    blt = next(g for g in late["gauges"] if g["lid"] == "BLTN7")
    assert blt["online"] is False and "Offline" in blt["note"]
