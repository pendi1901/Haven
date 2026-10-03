"""Least-risky routing (spec §7.5, §7.7 steps 4-5, 7).

- One Dijkstra search from the user's nearest node gives travel cost to every
  candidate destination at once (equivalent to the virtual-sink formulation, but
  it also yields the backup destination without a second search).
- Edges are removed when closed, flooded or at risk now, reported blocked by the
  user, or forecast (official NOAA forecast) to flood before the estimated arrival
  plus a 15-minute buffer. Fords and river bridges at minor stage or higher cost x5.
- Wording: "Least risky route in this data", never "safe route".
"""

from __future__ import annotations

import heapq
import logging
import threading
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Iterable
from urllib.parse import quote, urlencode

import networkx as nx
import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, shape

from app.config import (
    CAR_CAPACITY, DESTINATION_MARGIN_M, FORD_PENALTY, HIGH_GROUND_CANDIDATES, PET_PREFERENCE_SLACK,
    RIVER_SEVERITY, ROUTE_BUFFER_MIN, WALK_SPEED_MPS, WORK_CRS,
)
from app.geo.road_impact import AT_RISK, CLOSED, FLOODED, ImpactResult, locate
from app.geo.roads import WALK_ONLY_HIGHWAYS, bearing, load_graph
from app.models import Companions, Destination, LatLon, Route, RouteResult, RouteStep, Source

if TYPE_CHECKING:
    from app.state import Haven, World

log = logging.getLogger(__name__)

REFUGE_AMENITIES = {"community_centre", "school", "library", "place_of_worship", "townhall",
                    "college", "university", "social_facility"}
# Clean-air / cooling trips: public buildings likely to be open with AC.
CENTER_AMENITIES = {"library", "community_centre", "townhall"}
ACCESSIBLE_SLACK = 2.0
ACCESSIBLE_EXTRA_S = 10 * 60
MAX_SNAP_M = 600.0
START_RADIUS_M = 120.0
START_CANDIDATES = 8
DEST_SNAP_M = 300.0
CUTOFF_S = {"walk": 3 * 3600, "drive": 90 * 60}
HIGH_GROUND_CORRIDOR_M = 1500.0
WAYPOINT_OFFSET_M = 50.0
MAX_WAYPOINTS = 3


def walk_speed(c: Companions | None) -> float:
    if c is None:
        return WALK_SPEED_MPS["limited_mobility"]  # cautious default (spec §7.7 step 3)
    if c.limited_mobility:
        return WALK_SPEED_MPS["limited_mobility"]
    if c.kids or c.older_adults:
        return WALK_SPEED_MPS["kids_or_older"]
    return WALK_SPEED_MPS["default"]


@dataclass
class GraphData:
    mode: str
    G: nx.MultiDiGraph
    nodes: np.ndarray
    px: np.ndarray
    py: np.ndarray
    tree: cKDTree
    node_z: np.ndarray
    node_ridx: np.ndarray
    node_W: np.ndarray
    node_pos: dict[int, int]
    river_dist: np.ndarray


@dataclass
class Candidate:
    dest: Destination
    node: int
    cost: float = math.inf
    priority: int = 0  # 0 official shelter/refuge/hospital, 1 high ground


@dataclass
class _Search:
    pred: dict
    dist: dict
    removed: set
    limit: datetime | None


@dataclass
class RouteRequest:
    lat: float
    lon: float
    mode: str
    t: datetime
    companions: Companions | None = None
    needs_help: bool = False
    blocked: list[str] = field(default_factory=list)
    deadline: datetime | None = None  # t_flood_here, if known
    include_high_ground: bool = True
    purpose: str = "flood"  # flood | center (clean air / cooling) | fire


class Router:
    def __init__(self, ctx: "Haven"):
        self.ctx = ctx
        self._graphs: dict[str, GraphData] = {}
        self._graph_locks: dict[str, threading.Lock] = {}
        self._dest_cache: dict[tuple[str, str], tuple[int, float] | None] = {}

    # -- graph loading -------------------------------------------------------

    def graph(self, mode: str) -> GraphData:
        if mode in self._graphs:
            return self._graphs[mode]
        # Concurrent first requests wait for one build instead of each building it.
        with self._graph_locks.setdefault(mode, threading.Lock()):
            if mode in self._graphs:
                return self._graphs[mode]
            return self._build_graph(mode)

    def _build_graph(self, mode: str) -> GraphData:
        region = self.ctx.region
        G = load_graph(region.key, region.data_dir, mode)
        nodes = np.array(list(G.nodes))
        px = np.array([G.nodes[n]["px"] for n in nodes])
        py = np.array([G.nodes[n]["py"] for n in nodes])
        node_z = self.ctx.dem.sample(px, py)
        ridx, _, W = locate(self.ctx.impact.full_reaches(), px, py, corridor=HIGH_GROUND_CORRIDOR_M)
        river_dist = shapely.distance(shapely.points(px, py), self.ctx.rivers_work)
        gd = GraphData(mode=mode, G=G, nodes=nodes, px=px, py=py, tree=cKDTree(np.column_stack([px, py])),
                       node_z=node_z, node_ridx=ridx, node_W=W,
                       node_pos={int(n): i for i, n in enumerate(nodes)}, river_dist=river_dist)
        self._graphs[mode] = gd
        log.info("router: %s graph ready (%d nodes)", mode, len(nodes))
        return gd

    def nearest_node(self, gd: GraphData, lat: float, lon: float) -> tuple[int, float]:
        x, y = self.ctx.impact.to_work.transform(lon, lat)
        d, i = gd.tree.query([x, y])
        return int(gd.nodes[i]), float(d)

    # -- edge costs ------------------------------------------------------------

    def _edge_state(self, mode: str, w: "World", blocked: Iterable[str]):
        """Per-request lookup tables keyed by edge id for this graph."""
        imp: ImpactResult = w.impact
        removed_now: set[str] = set()
        first_flood: dict[str, datetime] = {}
        penalty: dict[str, float] = {}
        gauge_sev = {g.lid: RIVER_SEVERITY[g.category_now] for g in w.gauges}
        reach_gauges = {r["id"]: [r["upstream"], r["downstream"]] for r in self.ctx.impact.reaches_meta}
        for e in self.ctx.impact.edges:
            if e["graph"] != mode:
                continue
            i = e["i"]
            st = imp.status_now[i]
            if st in (AT_RISK, FLOODED, CLOSED):
                removed_now.add(e["eid"])
            ff = imp.first_flood[i]
            if ff is not None:
                first_flood[e["eid"]] = ff
            if e["ford"]:
                penalty[e["eid"]] = FORD_PENALTY
            elif e["bridge"] and e["crosses_river"]:
                sev = max((gauge_sev.get(lid, 0) for lid in reach_gauges.get(e["reach"], []) if lid), default=0)
                if sev >= RIVER_SEVERITY["minor"]:
                    penalty[e["eid"]] = FORD_PENALTY
        blocked_pairs = set()
        for b in blocked:
            parts = b.split("-")
            if len(parts) >= 2:
                blocked_pairs.add((int(parts[0]), int(parts[1])))
                blocked_pairs.add((int(parts[1]), int(parts[0])))
        return removed_now, first_flood, penalty, blocked_pairs

    def _search(self, gd: GraphData, starts: dict[int, float], t: datetime, speed: float | None, tables,
                limit: datetime | None):
        removed_now, first_flood, penalty, blocked_pairs = tables
        removed = set(removed_now)
        if limit is not None:
            removed |= {eid for eid, ff in first_flood.items() if ff <= limit}
        mode = gd.mode

        def cost(d: dict) -> float | None:
            eid = d["eid"]
            if eid in removed:
                return None
            base = d.get("travel_time") if mode == "drive" else d["length"] / speed
            if base is None:
                base = d["length"] / 8.0
            return base * penalty.get(eid, 1.0)

        def weight(u, v, dd):
            if (u, v) in blocked_pairs:
                return None
            best = None
            for d in dd.values():
                c = cost(d)
                if c is not None and (best is None or c < best):
                    best = c
            return best

        dist, pred = dijkstra_seeded(gd.G, starts, weight, CUTOFF_S[mode])
        return _Search(pred=pred, dist=dist, removed=removed, limit=limit), cost

    def start_nodes(self, gd: GraphData, lat: float, lon: float, speed: float | None, w: "World") -> dict[int, float]:
        """Road nodes the user can reach on foot from where they stand: up to 8 within
        120 m whose straight walk does not cross the current estimated flood extent.
        Each is seeded with that walk's time. Snapping to only the nearest node
        would start a dry user on a flooded street."""
        x, y = self.ctx.impact.to_work.transform(lon, lat)
        k = min(START_CANDIDATES, len(gd.nodes))
        dists, idxs = gd.tree.query([x, y], k=k)
        extent = shape(w.impact.extent_now) if w.impact.extent_now else None
        walk = speed or WALK_SPEED_MPS["default"]
        starts: dict[int, float] = {}
        for d, i in zip(np.atleast_1d(dists), np.atleast_1d(idxs)):
            if d > START_RADIUS_M and starts:
                break
            if d > MAX_SNAP_M:
                break
            n = int(gd.nodes[i])
            nlon, nlat = gd.G.nodes[n]["x"], gd.G.nodes[n]["y"]
            if extent is not None and d > 5 and extent.intersects(LineString([(lon, lat), (nlon, nlat)])):
                continue
            starts[n] = float(d) / walk
        return starts

    # -- destinations ------------------------------------------------------------

    def _snap(self, gd: GraphData, key: str, lat: float, lon: float) -> tuple[int, float] | None:
        ck = (gd.mode, key)
        if ck not in self._dest_cache:
            n, d = self.nearest_node(gd, lat, lon)
            self._dest_cache[ck] = (n, d) if d <= DEST_SNAP_M else None
        return self._dest_cache[ck]

    def _water_max(self, W: np.ndarray, w: "World") -> np.ndarray:
        lv = w.impact.levels
        now = self.ctx.impact.water_at_samples(W, lv.now)
        if lv.fc.shape[1]:
            fc = self.ctx.impact.water_at_samples(W, lv.fc)
            fc_max = np.where(np.isfinite(fc), fc, -np.inf).max(axis=1)
            return np.fmax(now, np.where(np.isfinite(fc_max), fc_max, np.nan))
        return now

    def candidates(self, gd: GraphData, w: "World", needs_help: bool, high_ground: bool,
                   purpose: str = "flood") -> tuple[list[Candidate], list[str]]:
        notes: list[str] = []
        hazard_geoms = [shape(z.geometry) for z in w.zones if z.hazard in ("river_flood", "wildfire")]
        hazard_union = shapely.union_all(hazard_geoms) if hazard_geoms else None
        if hazard_union is not None:
            shapely.prepare(hazard_union)
        sites = []
        for s in w.shelters:
            sites.append(("open_shelter", s, Source.FEMA))
        for p in self.ctx.pois:
            if p["amenity"] == "hospital":
                if needs_help:
                    sites.append(("hospital", p, Source.OSM))
            elif p["amenity"] in (CENTER_AMENITIES if purpose == "center" else REFUGE_AMENITIES):
                sites.append(("refuge", p, Source.OSM))
        if not sites:
            return [], notes
        xs, ys = self.ctx.impact.to_work.transform([s[1]["lon"] for s in sites], [s[1]["lat"] for s in sites])
        xs, ys = np.asarray(xs), np.asarray(ys)
        z = self.ctx.dem.sample(xs, ys)
        ridx, _, W = locate(self.ctx.impact.full_reaches(), xs, ys, corridor=HIGH_GROUND_CORRIDOR_M)
        wmax = self._water_max(W, w)
        rdist = shapely.distance(shapely.points(xs, ys), self.ctx.rivers_work)
        out: list[Candidate] = []
        for i, (kind, s, src) in enumerate(sites):
            if hazard_union is not None and hazard_union.covers(Point(s["lon"], s["lat"])):
                continue
            margin = None
            if ridx[i] >= 0 and np.isfinite(wmax[i]) and np.isfinite(z[i]):
                margin = float(z[i] - wmax[i])
                if margin < DESTINATION_MARGIN_M:
                    continue
            elif rdist[i] < 300:
                continue  # near a river reach we cannot evaluate: do not send people there
            snap = self._snap(gd, s["id"], s["lat"], s["lon"])
            if not snap:
                continue
            if kind == "open_shelter":
                label = "Open shelter"
            elif kind == "hospital":
                label = "Hospital"
            else:
                label = "Higher ground, not an official shelter, check if open"
            out.append(Candidate(
                dest=Destination(id=s["id"], name=s["name"], kind=kind, label=label, lat=s["lat"], lon=s["lon"],
                                 address=s.get("address"), elevation_m=None if not np.isfinite(z[i]) else float(z[i]),
                                 margin_m=margin, pet_friendly=s.get("pet_friendly"), accessible=s.get("accessible"),
                                 source=src),
                node=snap[0], priority=0))
        if high_ground:
            wmax_nodes = self._water_max(gd.node_W, w)
            ok = (gd.node_ridx >= 0) & np.isfinite(wmax_nodes) & np.isfinite(gd.node_z) & \
                 (gd.node_z >= wmax_nodes + DESTINATION_MARGIN_M)
            # Only nodes that are high relative to a flood we can evaluate and whose
            # river is actually above normal; on a calm day every hill qualifies.
            for i in np.where(ok)[0]:
                n = int(gd.nodes[i])
                out.append(Candidate(
                    dest=Destination(id=f"hg-{gd.mode}-{n}", name="Higher ground", kind="high_ground",
                                     label="Higher ground, not an official shelter",
                                     lat=float(gd.G.nodes[n]["y"]), lon=float(gd.G.nodes[n]["x"]),
                                     elevation_m=float(gd.node_z[i]), margin_m=float(gd.node_z[i] - wmax_nodes[i]),
                                     source=Source.HAVEN),
                    node=n, priority=1))
        return out, notes

    # -- main entry --------------------------------------------------------------

    def route(self, req: RouteRequest, w: "World") -> RouteResult:
        gd = self.graph(req.mode)
        speed = None if req.mode == "drive" else walk_speed(req.companions)
        _, snap_d = self.nearest_node(gd, req.lat, req.lon)
        if snap_d > MAX_SNAP_M:
            return RouteResult(route=None, backup=None, t=req.t,
                               message="Your location is outside the road network in this data.")
        source = self.start_nodes(gd, req.lat, req.lon, speed, w)
        if not source:
            return RouteResult(route=None, backup=None, t=req.t, message="No open route found in this data.")
        tables = self._edge_state(req.mode, w, req.blocked)
        cands, notes = self.candidates(gd, w, req.needs_help, req.include_high_ground, req.purpose)
        if not cands:
            return RouteResult(route=None, backup=None, t=req.t, message="No open route found in this data.")

        buffer = timedelta(minutes=ROUTE_BUFFER_MIN)
        trip = timedelta(0)
        best = None
        search = cost_fn = None
        for _ in range(4):
            limit = req.t + trip + buffer
            search, cost_fn = self._search(gd, source, req.t, speed, tables, limit)
            for c in cands:
                c.cost = search.dist.get(c.node, math.inf)
            best = self._choose(cands, req)
            if best is None:
                break
            new_trip = timedelta(seconds=best[0].cost)
            if new_trip <= trip:
                break
            trip = new_trip
        if best is not None and search is not None and search.limit is not None \
                and req.t + timedelta(seconds=best[0].cost) + buffer > search.limit:
            # Not converged: the chosen trip outlasts the forecast filter it was found
            # with. One more conservative pass with the longer trip; drop it if it still
            # does not fit.
            limit = req.t + timedelta(seconds=best[0].cost) + buffer
            search, cost_fn = self._search(gd, source, req.t, speed, tables, limit)
            for c in cands:
                c.cost = search.dist.get(c.node, math.inf)
            best = self._choose(cands, req)
            if best is not None and req.t + timedelta(seconds=best[0].cost) + buffer > limit:
                best = None
        if best is None or search is None:
            return RouteResult(route=None, backup=None, t=req.t, considered=len(cands),
                               message="No open route found in this data.")
        primary, backup, choose_notes = best
        r1 = self._build(gd, search, cost_fn, source, primary, req, w, speed, tables)
        r1.notes.extend(notes + choose_notes)
        r2 = self._build(gd, search, cost_fn, source, backup, req, w, speed, tables) if backup else None
        if req.mode == "drive" and req.companions and req.companions.count + 1 > CAR_CAPACITY:
            r1.notes.append(f"{req.companions.count + 1} people may not fit in one car.")
        return RouteResult(route=r1, backup=r2, t=req.t, considered=len(cands))

    def _choose(self, cands: list[Candidate], req: RouteRequest):
        reach = [c for c in cands if math.isfinite(c.cost)]
        if not reach:
            return None
        notes: list[str] = []
        comp = req.companions
        limited = comp is None or comp.limited_mobility
        deadline = req.deadline - timedelta(minutes=ROUTE_BUFFER_MIN) if req.deadline else None

        def in_time(c: Candidate) -> bool:
            return deadline is None or req.t + timedelta(seconds=c.cost) <= deadline

        official = sorted((c for c in reach if c.priority == 0), key=lambda c: c.cost)
        high = sorted((c for c in reach if c.priority == 1), key=lambda c: c.cost)
        timely = [c for c in official if in_time(c)]
        if limited and timely:
            # Limited mobility: only accessible destinations, unless accessibility data
            # would force a much longer trip (OSM tags are sparse); then the nearest,
            # flagged so the user can call ahead.
            acc = [c for c in timely if c.dest.accessible]
            nearest = timely[0].cost
            if acc and acc[0].cost <= max(ACCESSIBLE_SLACK * nearest, nearest + ACCESSIBLE_EXTRA_S):
                timely = acc
            elif acc:
                notes.append(f"The nearest site marked wheelchair-accessible is {acc[0].dest.name}, "
                             f"{(acc[0].cost - nearest) / 60:.0f} min farther. Call ahead to check access.")
            else:
                notes.append("No reachable destination in this data is marked wheelchair-accessible. Call ahead.")
        primary = timely[0] if timely else None
        if primary and comp and comp.pets:
            pet = [c for c in timely if c.dest.pet_friendly]
            if pet and pet[0].cost <= primary.cost * (1 + PET_PREFERENCE_SLACK):
                primary = pet[0]
            elif not pet:
                notes.append("No pet-friendly shelter is listed nearby; ask before you arrive.")
        if primary is None:
            hg = [c for c in high if in_time(c)]
            primary = hg[0] if hg else None
        if primary is None:
            # Nothing arrives before the deadline: return the quickest option anyway;
            # the decision engine compares its arrival with the deadline.
            if not (official or high):
                return None
            primary = min(official + high, key=lambda c: c.cost)
        backup = None
        if primary.priority == 0:
            backup = next((c for c in high if c.dest.id != primary.dest.id), None) or \
                     next((c for c in official if c.dest.id != primary.dest.id), None)
        else:
            far = [c for c in official if c.dest.id != primary.dest.id]
            backup = far[0] if far else next((c for c in high if c.node != primary.node
                                               and _dist_nodes(c, primary) > 200), None)
        return primary, backup, notes

    # -- route assembly -------------------------------------------------------------

    def _build(self, gd: GraphData, search: _Search, cost_fn, source: dict[int, float], cand: Candidate,
               req: RouteRequest, w: "World", speed: float | None, tables) -> Route:
        nodes = [cand.node]
        while search.pred.get(nodes[-1]) is not None:
            nodes.append(search.pred[nodes[-1]])
        nodes.reverse()
        first = nodes[0]
        lead_m = source[first] * (speed or WALK_SPEED_MPS["default"])
        edges = []
        for u, v in zip(nodes[:-1], nodes[1:]):
            dd = gd.G.get_edge_data(u, v)
            k, d = min(((k, d) for k, d in dd.items() if cost_fn(d) is not None), key=lambda kd: cost_fn(kd[1]))
            edges.append((u, v, k, d))
        coords = _route_coords(gd.G, edges) or [[round(gd.G.nodes[first]["x"], 6), round(gd.G.nodes[first]["y"], 6)]]
        coords = [[round(req.lon, 6), round(req.lat, 6)]] + coords  # walk from the user to the first road node
        length = lead_m + sum(d["length"] for *_, d in edges)
        duration = cand.cost
        arrive = req.t + timedelta(seconds=duration)
        ff = [tables[1][d["eid"]] for *_, d in edges if d["eid"] in tables[1]]
        deadline = min(ff) - timedelta(minutes=ROUTE_BUFFER_MIN) if ff else None
        slack = (deadline - arrive).total_seconds() / 60 if deadline else None
        steps = _instructions(gd.G, edges, cand.dest)
        fastest = self._fastest_nodes(gd, first, cand.node, speed)
        hazard_pts = self._hazard_lines(gd.mode, w)
        gurl, wps = google_maps_url(
            (req.lat, req.lon), (cand.dest.lat, cand.dest.lon), coords, nodes, fastest, hazard_pts,
            req.mode, node_xy=lambda n: (gd.G.nodes[n]["x"], gd.G.nodes[n]["y"]))
        return Route(
            mode=req.mode, destination=cand.dest,
            geometry={"type": "LineString", "coordinates": coords},
            edge_ids=[d["eid"] for *_, d in edges],
            edge_starts_m=[round(lead_m + float(x), 1) for x in np.concatenate([[0.0], np.cumsum([d["length"] for *_, d in edges])[:-1]])] if edges else [],
            distance_m=length, duration_s=duration,
            depart_at=req.t, arrive_at=arrive, deadline=deadline, slack_min=slack, steps=steps,
            speed_mps=speed, google_maps_url=gurl, apple_maps_url=apple_maps_url(
                (req.lat, req.lon), (cand.dest.lat, cand.dest.lon), req.mode),
            waypoints=[LatLon(lat=a, lon=b) for a, b in wps],
        )

    def _fastest_nodes(self, gd: GraphData, s: int, d: int, speed: float | None) -> list[int]:
        """Plain fastest path ignoring hazards (approximates what Google would pick)."""
        def weight(u, v, dd):
            return min((x.get("travel_time") if gd.mode == "drive" and x.get("travel_time") else x["length"] / (speed or 1.3))
                       for x in dd.values())
        try:
            return nx.dijkstra_path(gd.G, s, d, weight=weight)
        except nx.NetworkXNoPath:
            return []

    def _hazard_lines(self, mode: str, w: "World") -> list[LineString]:
        out = []
        imp = w.impact
        for e in self.ctx.impact.edges:
            if e["graph"] != mode:
                continue
            i = e["i"]
            if imp.status_now[i] >= AT_RISK or imp.first_flood[i] is not None:
                out.append(LineString(e["coords"]) if len(e["coords"]) > 1 else Point(e["coords"][0]))
        return out


def dijkstra_seeded(G: nx.MultiDiGraph, starts: dict[int, float], weight, cutoff: float):
    """Dijkstra from several start nodes with initial costs. Returns (dist, pred)
    where pred maps node -> previous node (None for a start node)."""
    dist: dict[int, float] = {}
    pred: dict[int, int | None] = {}
    seen: dict[int, float] = {}
    heap: list[tuple[float, int, int, int | None]] = []
    c = 0
    for n, d0 in starts.items():
        seen[n] = d0
        heapq.heappush(heap, (d0, c, n, None))
        c += 1
    adj = G._adj
    while heap:
        d, _, u, p = heapq.heappop(heap)
        if u in dist:
            continue
        dist[u] = d
        pred[u] = p
        for v, dd in adj[u].items():
            if v in dist:
                continue
            wgt = weight(u, v, dd)
            if wgt is None:
                continue
            nd = d + wgt
            if nd > cutoff:
                continue
            if v not in seen or nd < seen[v]:
                seen[v] = nd
                heapq.heappush(heap, (nd, c, v, u))
                c += 1
    return dist, pred


def _dist_nodes(a: Candidate, b: Candidate) -> float:
    return _haversine_m(a.dest.lat, a.dest.lon, b.dest.lat, b.dest.lon)


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _edge_coords(G, u, v, d) -> list[list[float]]:
    c = d["lonlat"].tolist()
    ux, uy = G.nodes[u]["x"], G.nodes[u]["y"]
    if (c[0][0] - ux) ** 2 + (c[0][1] - uy) ** 2 > (c[-1][0] - ux) ** 2 + (c[-1][1] - uy) ** 2:
        c = c[::-1]
    return c


def _route_coords(G, edges) -> list[list[float]]:
    coords: list[list[float]] = []
    for u, v, _, d in edges:
        c = _edge_coords(G, u, v, d)
        coords.extend(c if not coords else c[1:])
    return [[round(x, 6), round(y, 6)] for x, y in coords]


def _street_label(d: dict) -> str:
    if d.get("name"):
        return d["name"]
    hw = d.get("highway", "")
    if hw in WALK_ONLY_HIGHWAYS:
        return "the path" if hw != "steps" else "the steps"
    if hw in ("service", "living_street"):
        return "the service road"
    return "the unnamed road"


def _instructions(G, edges, dest: Destination) -> list[RouteStep]:
    if not edges:
        return [RouteStep(instruction=f"Arrive at {dest.name}", street=None, distance_m=0, lat=dest.lat, lon=dest.lon)]
    groups: list[dict] = []
    for u, v, _, d in edges:
        c = _edge_coords(G, u, v, d)
        name = _street_label(d)
        b0 = bearing(c[0][0], c[0][1], c[1][0], c[1][1])
        b1 = bearing(c[-2][0], c[-2][1], c[-1][0], c[-1][1])
        if groups and groups[-1]["name"] == name:
            groups[-1]["dist"] += d["length"]
            groups[-1]["b1"] = b1
        else:
            groups.append({"name": name, "dist": d["length"], "b0": b0, "b1": b1, "lat": c[0][1], "lon": c[0][0]})
    steps = []
    for i, g in enumerate(groups):
        if i == 0:
            from app.hazards.base import compass
            text = f"Head {_compass_word(compass(g['b0']))} on {g['name']}"
            delta = None
        else:
            delta = (g["b0"] - groups[i - 1]["b1"] + 540) % 360 - 180
            a = abs(delta)
            side = "right" if delta > 0 else "left"
            if a < 25:
                verb = "Continue onto"
            elif a < 60:
                verb = f"Bear {side} onto"
            elif a < 150:
                verb = f"Turn {side} onto"
            else:
                verb = "Make a U-turn onto"
            text = f"{verb} {g['name']}"
        steps.append(RouteStep(instruction=text, street=g["name"], distance_m=round(g["dist"], 1),
                               bearing_change=None if delta is None else round(delta, 1), lat=g["lat"], lon=g["lon"]))
    steps.append(RouteStep(instruction=f"Arrive at {dest.name}", street=None, distance_m=0, lat=dest.lat, lon=dest.lon))
    return steps


def _compass_word(c: str) -> str:
    return {"N": "north", "NE": "northeast", "E": "east", "SE": "southeast", "S": "south", "SW": "southwest",
            "W": "west", "NW": "northwest"}[c]


# ---------------------------------------------------------------------------
# Map handoff (spec §7.7 step 7)
# ---------------------------------------------------------------------------


def _point_along(coords: list[list[float]], cum: np.ndarray, dist: float) -> tuple[float, float]:
    dist = min(max(dist, 0.0), float(cum[-1]))
    i = int(np.searchsorted(cum, dist, side="right") - 1)
    i = min(i, len(coords) - 2)
    seg = cum[i + 1] - cum[i]
    f = 0.0 if seg <= 0 else (dist - cum[i]) / seg
    lon = coords[i][0] + (coords[i + 1][0] - coords[i][0]) * f
    lat = coords[i][1] + (coords[i + 1][1] - coords[i][1]) * f
    return lat, lon


def divergence_waypoints(coords: list[list[float]], haven_nodes: list[int], fastest_nodes: list[int],
                         hazards: list, node_xy, max_waypoints: int = MAX_WAYPOINTS) -> list[tuple[float, float]]:
    """Waypoints ~50 m past each point where Haven's route leaves the plain fastest
    route. At most `max_waypoints`; if there are more divergences, keep those
    nearest the hazards, returned in route order."""
    if len(coords) < 2 or len(haven_nodes) < 2:
        return []
    fast_edges = set(zip(fastest_nodes[:-1], fastest_nodes[1:]))
    seg = [_haversine_m(a[1], a[0], b[1], b[0]) for a, b in zip(coords[:-1], coords[1:])]
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    # Distance along the route of each Haven node (closest coordinate to the node).
    node_dist = []
    j = 0
    for n in haven_nodes:
        x, y = node_xy(n)
        best_j, best_d = j, math.inf
        for k in range(j, len(coords)):
            dd = (coords[k][0] - x) ** 2 + (coords[k][1] - y) ** 2
            if dd < best_d:
                best_j, best_d = k, dd
            if dd < 1e-14:
                break
        j = best_j
        node_dist.append(cum[best_j])
    starts = []
    for i, (a, b) in enumerate(zip(haven_nodes[:-1], haven_nodes[1:])):
        diverged = (a, b) not in fast_edges
        prev_on = i == 0 or (haven_nodes[i - 1], a) in fast_edges
        if diverged and (prev_on or i == 0):
            starts.append(i)
    if not starts:
        return []
    wps = [(_point_along(coords, cum, node_dist[i] + WAYPOINT_OFFSET_M), node_dist[i]) for i in starts]
    if len(wps) > max_waypoints:
        if hazards:
            tree = shapely.STRtree(hazards)
            def hazard_dist(p):
                pt = Point(p[1], p[0])
                k = tree.nearest(pt)
                return hazards[k].distance(pt)
            wps = sorted(wps, key=lambda w: hazard_dist(w[0]))[:max_waypoints]
            wps.sort(key=lambda w: w[1])
        else:
            wps = wps[:max_waypoints]
    return [(round(p[0], 6), round(p[1], 6)) for p, _ in wps]


def google_maps_url(origin, dest, coords, haven_nodes, fastest_nodes, hazards, mode, node_xy):
    wps = divergence_waypoints(coords, haven_nodes, fastest_nodes, hazards, node_xy)
    params = {
        "api": "1",
        "origin": f"{origin[0]:.6f},{origin[1]:.6f}",
        "destination": f"{dest[0]:.6f},{dest[1]:.6f}",
        "travelmode": "walking" if mode == "walk" else "driving",
    }
    if wps:
        params["waypoints"] = "|".join(f"{a},{b}" for a, b in wps)
    return "https://www.google.com/maps/dir/?" + urlencode(params, quote_via=quote, safe=","), wps


def apple_maps_url(origin, dest, mode) -> str:
    return (f"https://maps.apple.com/?saddr={origin[0]:.6f},{origin[1]:.6f}"
            f"&daddr={dest[0]:.6f},{dest[1]:.6f}&dirflg={'w' if mode == 'walk' else 'd'}")
