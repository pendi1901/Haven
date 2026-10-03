"""FEMA open shelters (live) and OpenStreetMap candidate refuge sites (fallback).

FEMA's National Shelter System feed lists shelters that are officially OPEN.
OSM sites are never presented as shelters: they are labeled "Higher ground, not an
official shelter, check if open" (spec §7.5).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.config import RegionConfig, settings

REFUGE_AMENITIES = {"community_centre", "school", "library", "place_of_worship", "townhall",
                    "college", "university", "social_facility"}
ACCESSIBLE_VALUES = {"yes", "designated", "limited"}


async def fetch_open_shelters(client: httpx.AsyncClient, region: RegionConfig) -> list[dict]:
    w, s, e, n = region.bbox
    pad = 0.15
    r = await client.get(settings().fema_shelters_url, params={
        "where": "shelter_status='OPEN'",
        "geometry": f"{w - pad},{s - pad},{e + pad},{n + pad}",
        "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
        "outFields": "shelter_id,shelter_name,address,city,state,zip,shelter_status,evacuation_capacity,"
                     "total_population,pet_accommodations_code,wheelchair_accessible,ada_compliant,org_name,"
                     "hours_open,hours_close",
        "outSR": 4326, "f": "geojson",
    })
    r.raise_for_status()
    out = []
    for f in r.json().get("features", []):
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        pets = (p.get("pet_accommodations_code") or "").upper()
        out.append({
            "id": f"fema-{p.get('shelter_id')}",
            "name": p.get("shelter_name"),
            "address": ", ".join(x for x in (p.get("address"), p.get("city")) if x),
            "lat": lat, "lon": lon,
            "pet_friendly": None if not pets else pets not in ("NONE", "NO", "N"),
            "accessible": True if (str(p.get("wheelchair_accessible") or "").upper() in ("Y", "YES", "TRUE")
                                   or str(p.get("ada_compliant") or "").upper() in ("Y", "YES", "TRUE")) else None,
            "capacity": p.get("evacuation_capacity"),
            "population": p.get("total_population"),
            "org": p.get("org_name"),
        })
    return out


def load_osm_pois(region: RegionConfig) -> list[dict]:
    path: Path = region.data_dir / "osm" / "pois.geojson"
    if not path.exists():
        return []
    out = []
    for f in json.loads(path.read_text())["features"]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        out.append({
            "id": f"osm-{p['osm_id']}",
            "name": p["name"],
            "amenity": p["amenity"],
            "address": p.get("address"),
            "lat": lat, "lon": lon,
            "accessible": True if (p.get("wheelchair") or "") in ACCESSIBLE_VALUES else None,
        })
    return out
