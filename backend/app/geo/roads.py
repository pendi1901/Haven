"""OSM road graphs, river centerlines and candidate destination POIs.

Downloaded once with OSMnx and cached under data/<region>/osm/. Graphs are saved
as GraphML (portable) plus a pickle (fast startup).
"""

from __future__ import annotations

import ast
import logging
import math
import pickle
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
from pyproj import Transformer
from shapely.geometry import LineString, mapping

from app.config import WORK_CRS, RegionConfig

log = logging.getLogger(__name__)

EXTRA_WAY_TAGS = ["bridge", "tunnel", "ford", "layer", "wheelchair", "surface", "covered"]
WALK_ONLY_HIGHWAYS = {
    "footway", "path", "pedestrian", "steps", "cycleway", "track", "bridleway", "corridor",
}
POI_TAGS = {
    "amenity": [
        "hospital", "school", "library", "community_centre", "place_of_worship",
        "townhall", "social_facility", "college", "university", "fire_station",
    ],
    "healthcare": ["hospital"],
    "emergency": ["assembly_point"],
}


def osm_dir(region: RegionConfig) -> Path:
    return region.data_dir / "osm"


def _configure_osmnx(region: RegionConfig) -> None:
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(region.data_dir / "osm" / "http_cache")
    ox.settings.log_console = False
    ox.settings.requests_timeout = 300
    tags = set(ox.settings.useful_tags_way)
    tags.update(EXTRA_WAY_TAGS)
    ox.settings.useful_tags_way = sorted(tags)


def download_graphs(region: RegionConfig, force: bool = False) -> None:
    _configure_osmnx(region)
    out = osm_dir(region)
    out.mkdir(parents=True, exist_ok=True)
    for mode in ("drive", "walk"):
        gml = out / f"{mode}.graphml"
        if gml.exists() and not force:
            log.info("%s graph cached", mode)
            continue
        log.info("Downloading OSM %s graph for %s", mode, region.bbox)
        G = ox.graph_from_bbox(region.bbox, network_type=mode, simplify=False, retain_all=False)
        # Keep bridges, tunnels and fords as their own edges so that road impact can
        # treat them separately (spec §7.3 step 5).
        G = ox.simplify_graph(G, edge_attrs_differ=["bridge", "tunnel", "ford"])
        G = ox.truncate.largest_component(G, strongly=(mode == "drive"))
        if mode == "drive":
            G = ox.routing.add_edge_speeds(G)
            G = ox.routing.add_edge_travel_times(G)
        ox.save_graphml(G, gml)
        log.info("%s graph: %d nodes, %d edges", mode, G.number_of_nodes(), G.number_of_edges())
        (out / f"{mode}.pkl").unlink(missing_ok=True)


def download_rivers(region: RegionConfig, force: bool = False) -> Path:
    _configure_osmnx(region)
    out = osm_dir(region) / "rivers.geojson"
    if out.exists() and not force:
        return out
    names = {r.river_name for r in region.reaches}
    w, s, e, n = region.bbox
    pad = 0.02
    gdf = ox.features_from_bbox((w - pad, s - pad, e + pad, n + pad), {"waterway": ["river", "stream"]})
    gdf = gdf[gdf.geometry.type.isin(["LineString", "MultiLineString"])]
    gdf = gdf[gdf["name"].isin(names)][["name", "geometry"]].reset_index(drop=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out, driver="GeoJSON")
    log.info("Rivers: %d ways (%s)", len(gdf), ", ".join(sorted(names)))
    return out


def download_pois(region: RegionConfig, force: bool = False) -> Path:
    _configure_osmnx(region)
    out = osm_dir(region) / "pois.geojson"
    if out.exists() and not force:
        return out
    gdf = ox.features_from_bbox(region.bbox, POI_TAGS)
    gdf = gdf.reset_index()
    pts = gdf.to_crs(WORK_CRS).geometry.representative_point().to_crs("EPSG:4326")
    keep = []
    for i, row in gdf.iterrows():
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        amenity = row.get("amenity") if isinstance(row.get("amenity"), str) else None
        if row.get("healthcare") == "hospital":
            amenity = "hospital"
        if row.get("emergency") == "assembly_point":
            amenity = "assembly_point"
        if amenity is None:
            continue
        # Exclude clinics mis-tagged as hospitals with emergency=no? keep, but record.
        addr = " ".join(
            str(row.get(k)) for k in ("addr:housenumber", "addr:street")
            if isinstance(row.get(k), str)
        ) or None
        keep.append(
            {
                "osm_id": f"{row['element']}/{row['id']}",
                "name": name.strip(),
                "amenity": amenity,
                "wheelchair": row.get("wheelchair") if isinstance(row.get("wheelchair"), str) else None,
                "emergency": row.get("emergency") if isinstance(row.get("emergency"), str) else None,
                "address": addr,
                "geometry": pts.iloc[i],
            }
        )
    out_gdf = gpd.GeoDataFrame(keep, crs="EPSG:4326")
    out_gdf.to_file(out, driver="GeoJSON")
    log.info("POIs: %d named candidates", len(out_gdf))
    return out


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _parse_listish(v):
    if isinstance(v, str) and v.startswith("[") and v.endswith("]"):
        try:
            return ast.literal_eval(v)
        except (ValueError, SyntaxError):
            return v
    return v


def _has(v, value: str = "yes") -> bool:
    v = _parse_listish(v)
    if isinstance(v, list):
        return any(str(x) == value for x in v)
    return str(v) == value if v is not None else False


def _first_name(v) -> str | None:
    v = _parse_listish(v)
    if isinstance(v, list):
        v = next((x for x in v if isinstance(x, str)), None)
    return v if isinstance(v, str) else None


def _highway(v) -> str:
    v = _parse_listish(v)
    if isinstance(v, list):
        v = v[0]
    return str(v)


@lru_cache(maxsize=4)
def load_graph(region_key: str, data_dir: Path, mode: str) -> nx.MultiDiGraph:
    """Load a routing graph with normalized edge attributes and projected geometry.

    Each edge gets: `eid` (stable id "u-v-k"), `name`, `highway`, `is_bridge`,
    `is_tunnel`, `is_ford`, `length` (m), `xy` (Nx2 float array in WORK_CRS) and,
    for drive graphs, `travel_time` (s). Nodes get `x`,`y` (lon/lat) and `px`,`py`
    (WORK_CRS meters).
    """
    pkl = data_dir / "osm" / f"{mode}.pkl"
    gml = data_dir / "osm" / f"{mode}.graphml"
    if pkl.exists() and pkl.stat().st_mtime >= gml.stat().st_mtime:
        with open(pkl, "rb") as f:
            return pickle.load(f)

    log.info("Loading %s graph from GraphML (first run builds a pickle cache)", mode)
    G = ox.load_graphml(gml)
    to_work = Transformer.from_crs("EPSG:4326", WORK_CRS, always_xy=True)
    nodes = list(G.nodes)
    lons = np.array([G.nodes[n]["x"] for n in nodes])
    lats = np.array([G.nodes[n]["y"] for n in nodes])
    px, py = to_work.transform(lons, lats)
    for n, a, b in zip(nodes, px, py):
        G.nodes[n]["px"] = float(a)
        G.nodes[n]["py"] = float(b)

    for u, v, k, d in G.edges(keys=True, data=True):
        d["eid"] = f"{u}-{v}-{k}"
        d["name"] = _first_name(d.get("name")) or _first_name(d.get("ref"))
        d["highway"] = _highway(d.get("highway"))
        d["is_bridge"] = _has(d.get("bridge")) or _has(d.get("bridge"), "viaduct")
        d["is_tunnel"] = _has(d.get("tunnel")) or _has(d.get("tunnel"), "building_passage")
        d["is_ford"] = _has(d.get("ford"))
        geom = d.get("geometry")
        if geom is None:
            lonlat = [(G.nodes[u]["x"], G.nodes[u]["y"]), (G.nodes[v]["x"], G.nodes[v]["y"])]
        else:
            lonlat = list(geom.coords)
        lo, la = zip(*lonlat)
        ex, ey = to_work.transform(np.array(lo), np.array(la))
        d["xy"] = np.column_stack([ex, ey]).astype(np.float64)
        d["lonlat"] = np.array(lonlat, dtype=np.float64)
        d["length"] = float(d.get("length") or 0.0)
        for drop in ("geometry", "osmid", "ref", "lanes", "maxspeed", "access", "service",
                     "junction", "width", "est_width", "area", "surface", "covered", "layer",
                     "reversed", "oneway"):
            d.pop(drop, None)
    with open(pkl, "wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)
    return G


def edge_geojson(d: dict) -> dict:
    return mapping(LineString(d["lonlat"]))


def bearing(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360
