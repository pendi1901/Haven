"""Census tracts for the responder view, and the NCDOT post-Helene closure
snapshot used only for replay accuracy scoring (spec §11: never for routing).

- Tract geometry and 2020 population (POP100) come from TIGERweb (no key).
- Age 65+ and no-vehicle households come from ACS 5-year when CENSUS_API_KEY is
  set (the Census Data API requires a key as of 2026).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from app.config import RegionConfig, settings

log = logging.getLogger(__name__)

TIGER_TRACTS_2020 = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/MapServer/10/query"
ACS_URL = "https://api.census.gov/data/2022/acs/acs5"
AGE65_VARS = [f"B01001_0{n:02d}E" for n in range(20, 26)] + [f"B01001_0{n:02d}E" for n in range(44, 50)]
NCDOT_HELENE_LINES = ("https://services1.arcgis.com/aT1T0pU1ZdpuDk1t/arcgis/rest/services/"
                      "WNC_Post_Helene_Conditions_Map_10_08_WFL1/FeatureServer/1/query")


def tracts_path(region: RegionConfig) -> Path:
    return region.data_dir / "census" / "tracts.geojson"


def closures_path(region: RegionConfig) -> Path:
    return region.data_dir / "replay" / "ncdot_closures_2024-10-08.geojson"


def download_census(region: RegionConfig, client: httpx.Client, force: bool = False) -> None:
    out = tracts_path(region)
    if not out.exists() or force:
        w, s, e, n = region.bbox
        r = client.get(TIGER_TRACTS_2020, params={
            "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": "GEOID,NAME,POP100,HU100,STATE,COUNTY",
            "outSR": 4326, "f": "geojson"})
        r.raise_for_status()
        fc = r.json()
        key = settings().census_api_key
        if key:
            by_county: dict[tuple[str, str], list] = {}
            for f in fc["features"]:
                p = f["properties"]
                by_county.setdefault((p["STATE"], p["COUNTY"]), []).append(f)
            for (st, co), feats in by_county.items():
                acs = client.get(ACS_URL, params={
                    "get": ",".join(["B01003_001E", "B08201_001E", "B08201_002E"] + AGE65_VARS),
                    "for": "tract:*", "in": [f"state:{st}", f"county:{co}"], "key": key})
                if acs.status_code != 200:
                    log.warning("ACS request failed (%s); continuing without ACS fields", acs.status_code)
                    continue
                rows = acs.json()
                hdr = rows[0]
                table = {r_[hdr.index("state")] + r_[hdr.index("county")] + r_[hdr.index("tract")]: dict(zip(hdr, r_))
                         for r_ in rows[1:]}
                for f in feats:
                    a = table.get(f["properties"]["GEOID"])
                    if a:
                        f["properties"]["acs_population"] = int(a["B01003_001E"])
                        f["properties"]["age65"] = sum(int(a[v]) for v in AGE65_VARS)
                        f["properties"]["households"] = int(a["B08201_001E"])
                        f["properties"]["no_vehicle_households"] = int(a["B08201_002E"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(fc))
        log.info("census: %d tracts%s", len(fc["features"]), " (+ACS)" if key else " (no CENSUS_API_KEY: 2020 population only)")

    cpath = closures_path(region)
    if region.replay_start and (not cpath.exists() or force):
        w, s, e, n = region.bbox
        r = client.get(NCDOT_HELENE_LINES, params={
            "where": "EventName='2024 Hurricane Helene'", "geometry": f"{w},{s},{e},{n}",
            "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
            "outFields": "Id,CommonName,Reason,Condition,BridgeInvolved,StartDateET", "outSR": 4326,
            "f": "geojson"})
        r.raise_for_status()
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(r.text)
        log.info("NCDOT Helene closures (Oct 8 snapshot): %d lines", len(r.json().get("features", [])))
