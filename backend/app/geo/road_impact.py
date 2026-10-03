"""Road impact: water level (observed now or official forecast) -> road status.

A deterministic geospatial overlay, not a model (spec §7.3):

1. Water elevation at a gauge = NWPS gauge datum (NAVD88 ft) + stage, in meters.
   The DEM is also NAVD88 meters, so there is a single unit conversion.
2. The water surface along each reach is linearly interpolated by distance along
   the OSM river centerline between gauges (or between a gauge and a confluence).
   Only the reach between configured gauges is evaluated.
3. Road edges are sampled every few meters; samples within the corridor get the
   DEM elevation and the water elevation of their nearest river point.
   Edge depth = max over samples of (water elevation - ground elevation), which is
   the spec's "minimum road elevation vs nearest river point" evaluated per sample
   so long edges along a sloping river are handled correctly.
4. depth <= 0 dry, 0 < depth <= 0.5 ft at_risk, > 0.5 ft flooded.
5. Bridges / tunnels skip the DEM and inherit the worst status of their approaches.
6. Closures (live) override everything.

Because the water elevation at each sample is a fixed linear combination of the
gauge water elevations, it is precomputed as a weight matrix W (samples x gauges):
evaluating "now" and every official-forecast time step is a matrix product.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import shapely
from rasterio import features as rfeatures
from rasterio.transform import Affine
from scipy import ndimage
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import transform as shp_transform

from app.config import (
    CORRIDOR_M, FLOODED_DEPTH_FT, FT_PER_M, ROAD_SAMPLE_SPACING_M, WORK_CRS, RegionConfig,
)
from app.geo.elevation import dem_path, load_dem
from app.geo.roads import load_graph
from app.sources.nwps import load_gauge_meta

log = logging.getLogger(__name__)

DRY, AT_RISK, FLOODED, CLOSED = 0, 1, 2, 3
STATUS_NAMES = ["dry", "at_risk", "flooded", "closed"]
EXTENT_RES_M = 16.0
END_TOLERANCE_M = 2.0


def index_path(region: RegionConfig) -> Path:
    return region.data_dir / "derived" / "road_index.npz"


# ---------------------------------------------------------------------------
# Reach geometry
# ---------------------------------------------------------------------------


@dataclass
class Reach:
    id: str
    river_name: str
    line: LineString  # WORK_CRS, oriented upstream -> downstream
    length: float
    # Weights over gauges at the upstream end (s=0) and downstream end (s=L).
    w_up: np.ndarray
    w_down: np.ndarray
    outer_up: bool
    outer_down: bool
    upstream_lid: str
    downstream_lid: str | None


def _river_graph(lines: list[LineString]) -> nx.Graph:
    G = nx.Graph()
    for ln in lines:
        coords = [(round(x, 2), round(y, 2)) for x, y in ln.coords]
        for a, b in zip(coords[:-1], coords[1:]):
            if a != b:
                G.add_edge(a, b, w=float(np.hypot(a[0] - b[0], a[1] - b[1])))
    # Join ways whose endpoints are a few meters apart (OSM digitizing gaps).
    ends = [n for n in G.nodes if G.degree(n) == 1]
    if ends:
        tree = shapely.STRtree([Point(n) for n in ends])
        for i, n in enumerate(ends):
            for j in tree.query(Point(n), predicate="dwithin", distance=15.0):
                if j != i:
                    m = ends[j]
                    G.add_edge(n, m, w=float(np.hypot(n[0] - m[0], n[1] - m[1])))
    return G


def _nearest_node(G: nx.Graph, x: float, y: float) -> tuple:
    nodes = np.array(list(G.nodes))
    i = int(np.argmin((nodes[:, 0] - x) ** 2 + (nodes[:, 1] - y) ** 2))
    return tuple(nodes[i])


def build_reaches(region: RegionConfig, gauge_meta: dict[str, dict]) -> list[Reach]:
    from pyproj import Transformer

    rivers = gpd.read_file(region.data_dir / "osm" / "rivers.geojson").to_crs(WORK_CRS)
    to_work = Transformer.from_crs("EPSG:4326", WORK_CRS, always_xy=True)
    lids = [g.lid for g in region.gauges]
    G_count = len(lids)

    def onehot(lid: str) -> np.ndarray:
        v = np.zeros(G_count)
        v[lids.index(lid)] = 1.0
        return v

    def gauge_xy(lid: str) -> tuple[float, float]:
        m = gauge_meta[lid]
        return to_work.transform(m["lon"], m["lat"])

    built: dict[str, Reach] = {}
    for rc in region.reaches:  # joined reaches must come after the reach they join
        lines = []
        for geom in rivers[rivers["name"] == rc.river_name].geometry:
            lines.extend(getattr(geom, "geoms", [geom]))
        RG = _river_graph(lines)
        up_node = _nearest_node(RG, *gauge_xy(rc.upstream_lid))
        if rc.downstream_lid:
            down_node = _nearest_node(RG, *gauge_xy(rc.downstream_lid))
        else:
            host = built[rc.joins_reach]
            nodes = np.array(list(RG.nodes))
            d = shapely.distance(shapely.points(nodes[:, 0], nodes[:, 1]), host.line)
            down_node = tuple(nodes[int(np.argmin(d))])
        path = nx.shortest_path(RG, up_node, down_node, weight="w")
        line = LineString(path)
        if rc.downstream_lid:
            w_down = onehot(rc.downstream_lid)
            outer_down = True
        else:
            host = built[rc.joins_reach]
            s_conf = host.line.project(Point(path[-1]))
            f = s_conf / host.length
            w_down = host.w_up * (1 - f) + host.w_down * f
            outer_down = False
        built[rc.id] = Reach(
            id=rc.id, river_name=rc.river_name, line=line, length=line.length,
            w_up=onehot(rc.upstream_lid), w_down=w_down, outer_up=True, outer_down=outer_down,
            upstream_lid=rc.upstream_lid, downstream_lid=rc.downstream_lid,
        )
        log.info("Reach %s: %.1f km (%s -> %s)", rc.id, line.length / 1000, rc.upstream_lid,
                 rc.downstream_lid or f"confluence with {rc.joins_reach}")
    return list(built.values())


def locate(reaches: list[Reach], x: np.ndarray, y: np.ndarray, corridor: float = CORRIDOR_M):
    """For points, return (reach index or -1, distance to river, weight matrix)."""
    pts = shapely.points(x, y)
    n = len(x)
    dists = np.vstack([shapely.distance(pts, r.line) for r in reaches]) if reaches else np.empty((0, n))
    best = np.argmin(dists, axis=0) if reaches else np.full(n, -1)
    dbest = dists[best, np.arange(n)] if reaches else np.full(n, np.inf)
    W = np.zeros((n, len(reaches[0].w_up) if reaches else 0))
    ridx = np.full(n, -1, dtype=np.int16)
    for k, r in enumerate(reaches):
        sel = np.where((best == k) & (dbest <= corridor))[0]
        if not len(sel):
            continue
        s = shapely.line_locate_point(r.line, pts[sel])
        ok = np.ones(len(sel), dtype=bool)
        if r.outer_up:
            ok &= s > END_TOLERANCE_M
        if r.outer_down:
            ok &= s < r.length - END_TOLERANCE_M
        sel, s = sel[ok], s[ok]
        f = (s / r.length)[:, None]
        W[sel] = r.w_up[None, :] * (1 - f) + r.w_down[None, :] * f
        ridx[sel] = k
    return ridx, dbest, W


# ---------------------------------------------------------------------------
# Index build (prepare step "roadindex")
# ---------------------------------------------------------------------------


def _sample_line(xy: np.ndarray, spacing: float) -> np.ndarray:
    seg = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    total = seg.sum()
    if total <= 0:
        return xy[:1]
    n = max(2, int(np.ceil(total / spacing)) + 1)
    d = np.linspace(0, total, n)
    cum = np.concatenate([[0], np.cumsum(seg)])
    return np.column_stack([np.interp(d, cum, xy[:, 0]), np.interp(d, cum, xy[:, 1])])


def build_road_index(region: RegionConfig, force: bool = False) -> Path:
    out = index_path(region)
    if out.exists() and not force:
        log.info("road index cached")
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    gauge_meta = load_gauge_meta(region)
    reaches = build_reaches(region, gauge_meta)
    dem = load_dem(dem_path(region))
    river_lines = shapely.union_all([r.line for r in reaches])
    base = np.array([
        (gauge_meta[g.lid]["datum_navd88_ft"] + g.base_flow_stage_ft) / FT_PER_M
        if g.base_flow_stage_ft is not None and gauge_meta.get(g.lid, {}).get("datum_navd88_ft") is not None
        else -np.inf
        for g in region.gauges
    ])
    channel_dropped = 0
    all_rivers = gpd.read_file(region.data_dir / "osm" / "rivers.geojson").to_crs(WORK_CRS)
    river_any = shapely.union_all(all_rivers.geometry.values)

    arrays: dict[str, list] = {k: [] for k in (
        "s_edge", "s_x", "s_y", "s_z", "s_W")}
    edges_meta = []
    e_index = 0
    for mode in ("drive", "walk"):
        G = load_graph(region.key, region.data_dir, mode)
        corridor_poly = river_lines.buffer(CORRIDOR_M + 200)
        for u, v, k, d in G.edges(keys=True, data=True):
            xy = d["xy"]
            line = LineString(xy) if len(xy) > 1 else Point(xy[0])
            if not corridor_poly.intersects(line):
                continue
            pts = _sample_line(xy, ROAD_SAMPLE_SPACING_M)
            ridx, dist, W = locate(reaches, pts[:, 0], pts[:, 1])
            keep = ridx >= 0
            if not keep.any():
                continue
            special = d["is_bridge"] or d["is_tunnel"]
            crosses = bool(special and line.distance(river_any) < 25)
            em = {
                "i": e_index, "graph": mode, "eid": d["eid"], "u": u, "v": v,
                "name": d["name"], "highway": d["highway"], "bridge": d["is_bridge"],
                "tunnel": d["is_tunnel"], "ford": d["is_ford"], "crosses_river": crosses,
                "reach": reaches[int(np.bincount(ridx[keep]).argmax())].id,
                "coords": [[round(a, 6), round(b, 6)] for a, b in d["lonlat"]],
            }
            if not special:
                z = dem.sample(pts[keep, 0], pts[keep, 1])
                # Samples below the base-flow water surface are river channel, not road.
                # Gauges without a base-flow stage carry -1e9, which disables the
                # exclusion for samples that depend on them.
                in_channel = z < W[keep] @ np.where(np.isfinite(base), base, -1e9)
                channel_dropped += int(np.sum(in_channel & np.isfinite(z)))
                ok = np.isfinite(z) & ~in_channel
                if ok.any():
                    n_s = int(ok.sum())
                    arrays["s_edge"].append(np.full(n_s, e_index, dtype=np.int32))
                    arrays["s_x"].append(pts[keep, 0][ok])
                    arrays["s_y"].append(pts[keep, 1][ok])
                    arrays["s_z"].append(z[ok])
                    arrays["s_W"].append(W[keep][ok])
            edges_meta.append(em)
            e_index += 1
        log.info("road index: %s graph -> %d corridor edges so far", mode, e_index)

    # Approaches for bridges / tunnels: nearest non-special edges through connected
    # special edges, within the same graph (spec §7.3 step 5).
    by_graph_node: dict[tuple[str, int], list[int]] = {}
    for em in edges_meta:
        by_graph_node.setdefault((em["graph"], em["u"]), []).append(em["i"])
        by_graph_node.setdefault((em["graph"], em["v"]), []).append(em["i"])
    for em in edges_meta:
        if not (em["bridge"] or em["tunnel"]):
            continue
        seen, frontier, approaches = {em["i"]}, [em["i"]], set()
        while frontier and len(seen) < 50:
            cur = edges_meta[frontier.pop()]
            for node in (cur["u"], cur["v"]):
                for j in by_graph_node.get((cur["graph"], node), []):
                    if j in seen:
                        continue
                    seen.add(j)
                    if edges_meta[j]["bridge"] or edges_meta[j]["tunnel"]:
                        frontier.append(j)
                    else:
                        approaches.add(j)
        em["approaches"] = sorted(approaches)

    # Flood-extent grid: coarse DEM cells inside the corridor (display + zones).
    step = int(round(EXTENT_RES_M / dem.res))
    h, w = dem.height // step, dem.width // step
    zc = dem.data[: h * step, : w * step].reshape(h, step, w, step)
    zc = np.nanmin(zc, axis=(1, 3))
    cols, rows = np.meshgrid(np.arange(w), np.arange(h))
    cx = dem.x0 + (cols + 0.5) * EXTENT_RES_M
    cy = dem.y0 - (rows + 0.5) * EXTENT_RES_M
    corridor_mask = shapely.contains_xy(river_lines.buffer(CORRIDOR_M), cx, cy) & np.isfinite(zc)
    ci = np.where(corridor_mask.ravel())[0]
    cridx, _, cW = locate(reaches, cx.ravel()[ci], cy.ravel()[ci])
    okc = cridx >= 0
    ci, cW = ci[okc], cW[okc]
    # River cells: seeds for connectivity (flooding must connect to the channel).
    river_mask = rfeatures.rasterize(
        [(mapping(river_lines.buffer(EXTENT_RES_M)), 1)], out_shape=(h, w),
        transform=Affine(EXTENT_RES_M, 0, dem.x0, 0, -EXTENT_RES_M, dem.y0), dtype="uint8")

    cat = lambda k, dt: np.concatenate(arrays[k]).astype(dt) if arrays[k] else np.zeros(0, dt)  # noqa: E731
    s_W = np.concatenate(arrays["s_W"]) if arrays["s_W"] else np.zeros((0, len(region.gauges)))
    np.savez_compressed(
        out,
        s_edge=cat("s_edge", np.int32), s_x=cat("s_x", np.float64), s_y=cat("s_y", np.float64),
        s_z=cat("s_z", np.float32), s_W=s_W.astype(np.float32),
        cell_idx=ci.astype(np.int64), cell_z=zc.ravel()[ci].astype(np.float32), cell_W=cW.astype(np.float32),
        grid_shape=np.array([h, w]), grid_origin=np.array([dem.x0, dem.y0, EXTENT_RES_M]),
        river_mask=river_mask.astype(np.uint8),
    )
    meta = {
        "gauges": [g.lid for g in region.gauges],
        "reaches": [{"id": r.id, "river": r.river_name, "length_m": r.length,
                     "upstream": r.upstream_lid, "downstream": r.downstream_lid,
                     "coords": [list(c) for c in r.line.coords]} for r in reaches],
        "edges": edges_meta,
    }
    out.with_suffix(".json").write_text(json.dumps(meta))
    log.info("road index: %d edges, %d samples (%d channel samples dropped), %d extent cells",
             len(edges_meta), len(s_W), channel_dropped, len(ci))
    return out


# ---------------------------------------------------------------------------
# Runtime evaluation
# ---------------------------------------------------------------------------


@dataclass
class GaugeLevels:
    """Water elevations (m NAVD88) per gauge, now and on a forecast time grid."""

    now: np.ndarray  # (G,) NaN where unknown
    fc_times: list[datetime]  # (T,)
    fc: np.ndarray  # (G, T) NaN where no official forecast covers that time
    stale: np.ndarray  # (G,) bool: value held from an offline gauge


@dataclass
class ImpactResult:
    t: datetime
    status_now: np.ndarray  # (E,) int8
    depth_now_ft: np.ndarray  # (E,) float, NaN for bridges w/o approaches
    first_flood: list[datetime | None]  # (E,) official-forecast time edge floods
    first_flood_idx: np.ndarray  # (E,) int, -1 if none within horizon
    max_fc_depth_ft: np.ndarray
    stale_edges: np.ndarray  # (E,) bool
    levels: GaugeLevels
    extent_now: dict | None = None
    extent_forecast: dict | None = None
    flooded_ids: set[str] = field(default_factory=set)


class RoadImpact:
    def __init__(self, region: RegionConfig):
        self.region = region
        p = index_path(region)
        if not p.exists():
            raise FileNotFoundError(f"{p} missing: run `python -m app.prepare`")
        z = np.load(p)
        self.s_edge = z["s_edge"]
        self.s_x, self.s_y = z["s_x"], z["s_y"]
        self.s_z = z["s_z"].astype(np.float64)
        self.s_W = z["s_W"].astype(np.float64)
        self.cell_idx, self.cell_z, self.cell_W = z["cell_idx"], z["cell_z"].astype(np.float64), z["cell_W"].astype(np.float64)
        self.grid_shape = tuple(int(v) for v in z["grid_shape"])
        gx0, gy0, gres = z["grid_origin"]
        self.grid_transform = Affine(gres, 0, gx0, 0, -gres, gy0)
        self.river_mask = z["river_mask"].astype(bool)
        meta = json.loads(p.with_suffix(".json").read_text())
        self.gauges: list[str] = meta["gauges"]
        self.reaches_meta = meta["reaches"]
        self.edges: list[dict] = meta["edges"]
        self.E = len(self.edges)
        self.eid_index: dict[tuple[str, str], int] = {(e["graph"], e["eid"]): e["i"] for e in self.edges}
        # Sorted sample ranges per edge for reduceat.
        order = np.argsort(self.s_edge, kind="stable")
        self.s_edge, self.s_x, self.s_y = self.s_edge[order], self.s_x[order], self.s_y[order]
        self.s_z, self.s_W = self.s_z[order], self.s_W[order]
        self.sampled_edges, self.starts = np.unique(self.s_edge, return_index=True)
        self.special = [e["i"] for e in self.edges if e["bridge"] or e["tunnel"]]
        self.reaches = [
            Reach(id=r["id"], river_name=r["river"], line=LineString(r["coords"]), length=r["length_m"],
                  w_up=np.zeros(0), w_down=np.zeros(0), outer_up=True, outer_down=True,
                  upstream_lid=r["upstream"], downstream_lid=r["downstream"])
            for r in self.reaches_meta
        ]
        self._reach_objs: list[Reach] | None = None
        from pyproj import Transformer
        self.to_ll = Transformer.from_crs(WORK_CRS, "EPSG:4326", always_xy=True)
        self.to_work = Transformer.from_crs("EPSG:4326", WORK_CRS, always_xy=True)

    # -- helpers -----------------------------------------------------------

    def full_reaches(self) -> list[Reach]:
        if self._reach_objs is None:
            self._reach_objs = build_reaches(self.region, load_gauge_meta(self.region))
        return self._reach_objs

    def water_at_samples(self, W: np.ndarray, levels: np.ndarray) -> np.ndarray:
        """W (N,G) @ levels (G,) or (G,T) with NaN propagation only for used gauges."""
        lv = np.asarray(levels, dtype=np.float64)
        missing = ~np.isfinite(lv)
        filled = np.where(missing, 0.0, lv)
        out = W @ filled
        if missing.any():
            if lv.ndim == 1:
                bad = (W[:, missing] > 1e-9).any(axis=1)
                out[bad] = np.nan
            else:
                used = W > 1e-9  # (N,G)
                bad = used.astype(np.float64) @ missing.astype(np.float64) > 0  # (N,T)
                out[bad] = np.nan
        return out

    def _edge_max(self, sample_vals: np.ndarray) -> np.ndarray:
        """Reduce per-sample values to per-edge max (NaN-ignoring)."""
        shape = (self.E,) + sample_vals.shape[1:]
        out = np.full(shape, np.nan)
        if len(sample_vals):
            red = np.fmax.reduceat(sample_vals, self.starts, axis=0)
            out[self.sampled_edges] = red
        return out

    @staticmethod
    def classify(depth_ft: np.ndarray) -> np.ndarray:
        st = np.zeros(depth_ft.shape, dtype=np.int8)
        st[depth_ft > 0] = AT_RISK
        st[depth_ft > FLOODED_DEPTH_FT] = FLOODED
        return st

    # -- evaluation --------------------------------------------------------

    def evaluate(self, t: datetime, levels: GaugeLevels, closures: set[tuple[str, str]] | None = None,
                 with_extent: bool = True) -> ImpactResult:
        we_now = self.water_at_samples(self.s_W, levels.now)
        depth_now = self._edge_max((we_now - self.s_z) * FT_PER_M)
        T = len(levels.fc_times)
        if T:
            we_fc = self.water_at_samples(self.s_W, levels.fc)
            depth_fc = self._edge_max((we_fc - self.s_z[:, None]) * FT_PER_M)
        else:
            depth_fc = np.full((self.E, 0), np.nan)

        stale_g = levels.stale
        used = self.s_W > 1e-9
        stale_s = (used & stale_g[None, :]).any(axis=1) if stale_g.any() else np.zeros(len(self.s_W), bool)
        stale_e = np.zeros(self.E, bool)
        if len(stale_s):
            stale_e[self.sampled_edges] = np.maximum.reduceat(stale_s, self.starts)

        # Bridges / tunnels inherit their approaches' worst depth.
        for i in self.special:
            appr = self.edges[i].get("approaches") or []
            if appr:
                depth_now[i] = np.nanmax(depth_now[appr]) if np.isfinite(depth_now[appr]).any() else np.nan
                if T:
                    sub = depth_fc[appr]
                    depth_fc[i] = np.where(np.isfinite(sub).any(axis=0), np.nanmax(np.where(np.isfinite(sub), sub, -np.inf), axis=0), np.nan)
                stale_e[i] = stale_e[appr].any()

        status = self.classify(np.nan_to_num(depth_now, nan=-1.0))
        if closures:
            for key in closures:
                j = self.eid_index.get(key)
                if j is not None:
                    status[j] = CLOSED

        if T:
            fl = np.nan_to_num(depth_fc, nan=-1.0) > FLOODED_DEPTH_FT
            anyfl = fl.any(axis=1)
            idx = np.where(anyfl, fl.argmax(axis=1), -1)
            first = [levels.fc_times[k] if k >= 0 else None for k in idx]
            max_fc = np.nanmax(np.where(np.isfinite(depth_fc), depth_fc, -np.inf), axis=1)
            max_fc[~np.isfinite(max_fc)] = np.nan
        else:
            idx = np.full(self.E, -1)
            first = [None] * self.E
            max_fc = np.full(self.E, np.nan)

        res = ImpactResult(t=t, status_now=status, depth_now_ft=depth_now, first_flood=first,
                           first_flood_idx=idx, max_fc_depth_ft=max_fc, stale_edges=stale_e, levels=levels)
        if with_extent:
            res.extent_now = self.extent(levels.now)
            if T:
                fc_max = np.where(np.isfinite(levels.fc), levels.fc, -np.inf).max(axis=1)
                fc_max = np.where(np.isfinite(fc_max), np.maximum(fc_max, np.nan_to_num(levels.now, nan=-np.inf)), np.nan)
                res.extent_forecast = self.extent(fc_max) if np.isfinite(fc_max).any() else None
        return res

    def extent(self, gauge_we: np.ndarray) -> dict | None:
        """Estimated flood extent polygon (GeoJSON, WGS84) for given gauge levels.

        Cells within the corridor whose ground is below the interpolated water
        surface AND that connect to the river channel. A terrain overlay of the
        official level, not an official inundation map.
        """
        we = self.water_at_samples(self.cell_W, gauge_we)
        wet = np.nan_to_num(we - self.cell_z, nan=-1.0) > 0
        h, w = self.grid_shape
        mask = np.zeros(h * w, dtype=bool)
        mask[self.cell_idx[wet]] = True
        mask = mask.reshape(h, w)
        if not mask.any():
            return None
        lab, n = ndimage.label(mask | self.river_mask)
        keep = np.unique(lab[self.river_mask & (lab > 0)])
        mask = np.isin(lab, keep) & mask
        mask = ndimage.binary_opening(mask, iterations=1) | (mask & self.river_mask)
        if not mask.any():
            return None
        polys = [shape(g) for g, v in rfeatures.shapes(mask.astype(np.uint8), mask=mask, transform=self.grid_transform) if v == 1]
        if not polys:
            return None
        geom = shapely.union_all(polys).simplify(EXTENT_RES_M * 0.6)
        geom = shp_transform(lambda x, y, z=None: self.to_ll.transform(x, y), geom)
        return mapping(geom)

    # -- point queries -------------------------------------------------------

    def point_weights(self, lat: float, lon: float):
        x, y = self.to_work.transform(lon, lat)
        ridx, dist, W = locate(self.full_reaches(), np.array([x]), np.array([y]))
        return x, y, int(ridx[0]), float(dist[0]), W[0]

    def edges_near(self, lat: float, lon: float, radius_m: float, graph: str | None = None) -> list[int]:
        x, y = self.to_work.transform(lon, lat)
        out = []
        for e in self._edge_geoms():
            if graph and self.edges[e[0]]["graph"] != graph:
                continue
            if e[1].distance(Point(x, y)) <= radius_m:
                out.append(e[0])
        return out

    @lru_cache(maxsize=1)
    def _edge_geoms(self) -> list[tuple[int, LineString]]:
        out = []
        for e in self.edges:
            lo, la = zip(*e["coords"])
            xs, ys = self.to_work.transform(np.array(lo), np.array(la))
            out.append((e["i"], LineString(np.column_stack([xs, ys])) if len(xs) > 1 else Point(xs[0], ys[0])))
        return out

    def edge_tree(self):
        geoms = [g for _, g in self._edge_geoms()]
        return shapely.STRtree(geoms)


def utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def forecast_grid(t: datetime, horizon_h: float, step_min: int = 30) -> list[datetime]:
    t0 = utc(t)
    n = int(horizon_h * 60 // step_min)
    return [t0 + timedelta(minutes=step_min * (i + 1)) for i in range(n)]
