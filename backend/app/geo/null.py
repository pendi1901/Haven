"""Stand-ins for places without prepared terrain and road data.

Live mode works anywhere NWS covers: alerts, forecasts, nearby river gauges and
their official forecasts all come from a point. The road-flood overlay and routing
need a prepared region (DEM + road graph + river reaches); outside one these
objects make every overlay-dependent step return "nothing known" instead of
inventing an answer.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import shapely
from pyproj import Transformer

from app.config import WORK_CRS
from app.geo.road_impact import GaugeLevels, ImpactResult
from app.models import RouteResult

NO_ROADS_MESSAGE = ("Haven doesn't have road and terrain data for this area yet, so it can't check which "
                    "roads are flooding or plan a route. Follow official instructions and use the shelters listed.")


class NullDem:
    source = "no terrain data for this area"

    def sample(self, x, y) -> np.ndarray:
        return np.full(np.shape(x), np.nan)

    def sample_one(self, x: float, y: float) -> None:
        return None


class NullImpact:
    """A road index with no roads and no river reaches."""

    def __init__(self) -> None:
        self.gauges: list[str] = []
        self.edges: list[dict] = []
        self.E = 0
        self.eid_index: dict = {}
        self.reaches_meta: list[dict] = []
        self.to_work = Transformer.from_crs("EPSG:4326", WORK_CRS, always_xy=True)
        self.to_ll = Transformer.from_crs(WORK_CRS, "EPSG:4326", always_xy=True)
        self._tree = shapely.STRtree([])

    def evaluate(self, t: datetime, levels: GaugeLevels, closures=None, with_extent: bool = True) -> ImpactResult:
        z = np.zeros(0)
        return ImpactResult(t=t, status_now=np.zeros(0, dtype=np.int8), depth_now_ft=z, first_flood=[],
                            first_flood_idx=np.zeros(0, dtype=int), max_fc_depth_ft=z,
                            stale_edges=np.zeros(0, dtype=bool), levels=levels)

    def water_at_samples(self, W: np.ndarray, levels: np.ndarray) -> np.ndarray:
        return np.full((W.shape[0],) + np.shape(levels)[1:], np.nan)

    def point_weights(self, lat: float, lon: float):
        x, y = self.to_work.transform(lon, lat)
        return x, y, -1, float("inf"), np.zeros(0)

    def full_reaches(self) -> list:
        return []

    def edge_tree(self):
        return self._tree

    def _edge_geoms(self) -> list:
        return []

    def edges_near(self, *args, **kwargs) -> list[int]:
        return []


class NullRouter:
    def route(self, req, w) -> RouteResult:
        return RouteResult(route=None, backup=None, t=req.t, message=NO_ROADS_MESSAGE)

    def graph(self, mode: str):
        raise RuntimeError("no road graph for this area")
