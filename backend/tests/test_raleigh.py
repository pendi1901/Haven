"""Raleigh overlay (Crabtree and Walnut Creeks), checked against the prepared index."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from app.config import FT_PER_M, REGIONS
from app.geo.road_impact import GaugeLevels, RoadImpact
from app.sources.nwps import load_gauge_meta

RALEIGH = REGIONS["raleigh"]
T = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.skipif(not RALEIGH.has_overlay, reason="run `REGION=raleigh python -m app.prepare` first")


@pytest.fixture(scope="module")
def impact() -> RoadImpact:
    return RoadImpact(RALEIGH)


def levels(impact: RoadImpact, **stage_ft: float) -> GaugeLevels:
    """Base flow everywhere, overridden per gauge; gauges without a datum are NaN."""
    meta = load_gauge_meta(RALEIGH)
    stage = {g.lid: g.base_flow_stage_ft for g in RALEIGH.gauges} | stage_ft
    now = np.array([
        (meta[lid]["datum_navd88_ft"] + stage[lid]) / FT_PER_M
        if stage.get(lid) is not None and meta[lid].get("datum_navd88_ft") is not None else np.nan
        for lid in impact.gauges
    ])
    n = len(impact.gauges)
    return GaugeLevels(now=now, fc_times=[], fc=np.zeros((n, 0)), stale=np.zeros(n, bool))


def flagged_reaches(impact: RoadImpact, lv: GaugeLevels) -> set[str]:
    res = impact.evaluate(T, lv, with_extent=False)
    return {impact.edges[i]["reach"] for i in np.where(res.status_now > 0)[0]}


def test_base_flow_nothing_flooded(impact):
    assert flagged_reaches(impact, levels(impact)) == set()


def test_buck_jones_reach_ends_at_lake_johnson(impact):
    """The creek reaches the lake only through unnamed OSM ways; the reach must
    cross them and end at the inlet, where Lake Johnson's level applies."""
    reach = next(r for r in impact.reaches_meta if r["id"] == "walnut_buck_jones")
    assert (reach["upstream"], reach["downstream"]) == ("BKJN7", "JHSN7")
    lon, lat = impact.to_ll.transform(*reach["coords"][-1])
    assert abs(lat - 35.76800) < 2e-4 and abs(lon + 78.72230) < 2e-4
    assert reach["length_m"] > 1800


def test_ordinary_lake_rise_floods_nothing(impact):
    """Lake Johnson moves about half a foot without any flooding."""
    assert flagged_reaches(impact, levels(impact, JHSN7=343.16 + 1.0)) == set()


def test_walnut_minor_flood_stays_on_walnut(impact):
    meta = load_gauge_meta(RALEIGH)
    minor = {lid: meta[lid]["thresholds_ft"]["minor"] for lid in ("BKJN7", "WAWN7", "WSSN7", "WRLN7", "WALN7")}
    flagged = flagged_reaches(impact, levels(impact, **minor))
    assert flagged and all(r.startswith("walnut_") for r in flagged)
