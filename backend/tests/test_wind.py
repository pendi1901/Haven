"""Wind metric banding, forecast parsing and the wind verdicts."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from app.engine.decision import decide
from app.hazards.wind import Wind, _hourly_mph, wind_band
from app.models import CheckIn, LatLon, Profile, Source
from app.sources.base import SourceCache

from tests.conftest import T0, mk_input, mk_metric, mk_zone


def test_wind_band_takes_worse_of_gust_and_sustained():
    assert wind_band(20, 10) == ("Light", 0)
    assert wind_band(21, 15) == ("Breezy", 0)  # sustained band wins a severity tie
    assert wind_band(47, 20) == ("Strong", 2)  # Wind Advisory by gusts
    assert wind_band(30, 32) == ("Strong", 2)  # Wind Advisory by sustained
    assert wind_band(60, 25)[1] == 3  # High Wind Warning by gusts
    assert wind_band(None, 41)[1] == 3
    assert wind_band(None, None) == ("Light", 0)


def test_hourly_string_parsing():
    assert _hourly_mph("10 mph") == 10
    assert _hourly_mph("5 to 15 mph") == 15
    assert _hourly_mph(None) is None


def _module(tmp_path, fc: dict) -> Wind:
    cache = SourceCache(snapshot_dir=tmp_path)
    cache.put("nws_forecast", fc)
    ctx = SimpleNamespace(mode="live", cache=cache, modules={})
    return Wind(ctx)  # type: ignore[arg-type]


def _series(vals: list[float]) -> list[dict]:
    return [{"t": (T0 + timedelta(hours=i)).isoformat(), "value": v} for i, v in enumerate(vals)]


def test_metrics_now_and_peak(tmp_path):
    w = _module(tmp_path, {"wind_mph": _series([12, 20, 28, 15]), "wind_gust_mph": _series([22, 35, 52, 25])})
    now, peak = w.metrics_at(LatLon(lat=35.6, lon=-82.5), T0)
    assert (now.key, now.value, now.category, now.severity) == ("wind", 22, "Light", 0)
    assert "Sustained 12 mph" in now.detail
    assert (peak.key, peak.value, peak.category, peak.severity) == ("wind_max24", 52, "Strong", 2)
    assert peak.observed_or_valid_at == T0 + timedelta(hours=2)


def test_falls_back_to_hourly_wind_string(tmp_path):
    w = _module(tmp_path, {"hourly": [{"t": T0.isoformat(), "wind": "25 to 33 mph"}]})
    now = w.metrics_at(LatLon(lat=35.6, lon=-82.5), T0)[0]
    assert now.value == 33 and now.severity == 2
    assert "no gust forecast" in now.detail


def test_unavailable_without_forecast(tmp_path):
    w = _module(tmp_path, {})
    m = w.metrics_at(LatLon(lat=35.6, lon=-82.5), T0)[0]
    assert m.available is False


def _wind(v: float, cat: str, sev: int):
    return mk_metric("wind", v, cat, sev, source=Source.NWS)


def test_strong_wind_stay_indoors():
    v = decide(mk_input(metrics=[_wind(50, "Strong", 2)]))
    assert v.level == 2 and v.label == "Stay indoors"
    assert "50 mph" in v.reason
    assert any("away from windows" in s for s in v.steps)


def test_high_wind_shelter_in_place_mobile_home():
    v = decide(mk_input(metrics=[_wind(62, "High", 3)], profile=Profile(home_type="mobile_home"), hazard="wind"))
    assert v.level == 3
    assert any("Mobile homes" in s for s in v.steps)


def test_high_wind_outside_gets_inside():
    v = decide(mk_input(metrics=[_wind(62, "High", 3)], checkin=CheckIn(place="car"), hazard="wind"))
    assert v.level == 3
    assert v.steps[0].startswith("Get into a sturdy building")


def test_high_wind_warning_alert():
    v = decide(mk_input(alerts=[mk_zone("High Wind Warning")]))
    assert v.level == 3
    assert "High Wind Warning" in v.reason


def test_windy_is_limit_time_outdoors():
    v = decide(mk_input(metrics=[_wind(38, "Windy", 1)]))
    assert v.level == 1
