"""NHC active storms with official 5-day cone and forecast track (spec §5)."""

from __future__ import annotations

import io
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.config import settings


async def fetch_storms(client: httpx.AsyncClient) -> list[dict]:
    r = await client.get(settings().nhc_current_url)
    r.raise_for_status()
    storms = []
    for s in r.json().get("activeStorms", []):
        cone_zip = (s.get("trackCone") or {}).get("zipFile")
        track_zip = (s.get("forecastTrack") or {}).get("zipFile")
        entry = {
            "id": s.get("id"), "name": s.get("name"), "classification": s.get("classification"),
            "intensity_kt": s.get("intensity"), "lat": s.get("latitudeNumeric"), "lon": s.get("longitudeNumeric"),
            "issued": (s.get("forecastAdvisory") or {}).get("issuance") or s.get("lastUpdate"),
            "advisory_url": (s.get("publicAdvisory") or {}).get("url"),
            "cone": None, "track": [],
        }
        try:
            if cone_zip:
                entry["cone"] = await _read_shape(client, cone_zip, "_pgn.shp", geometry_only=True)
            if track_zip:
                entry["track"] = await _read_shape(client, track_zip, "_pts.shp", geometry_only=False,
                                                   issued=entry["issued"])
        except Exception:  # noqa: BLE001 - keep the storm without GIS
            pass
        storms.append(entry)
    return storms


async def _read_shape(client: httpx.AsyncClient, url: str, suffix: str, geometry_only: bool,
                      issued: str | None = None):
    import geopandas as gpd

    r = await client.get(url)
    r.raise_for_status()
    with tempfile.TemporaryDirectory() as tmp:
        zp = Path(tmp) / "f.zip"
        zp.write_bytes(r.content)
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            name = next((n for n in zf.namelist() if n.endswith(suffix)), None)
        if not name:
            return None if geometry_only else []
        gdf = gpd.read_file(f"zip://{zp}!{name}").to_crs("EPSG:4326")
    if geometry_only:
        import json
        return json.loads(gdf.geometry.to_json())["features"][0]["geometry"]
    issued_dt = datetime.fromisoformat(issued.replace("Z", "+00:00")) if issued else datetime.now(timezone.utc)
    synoptic = issued_dt.replace(minute=0, second=0, microsecond=0) - timedelta(hours=issued_dt.hour % 6)
    out = []
    for _, p in gdf.iterrows():
        tau = p.get("TAU")
        if tau is None:
            continue
        out.append({"t": (synoptic + timedelta(hours=float(tau))).isoformat(), "lat": p.geometry.y,
                    "lon": p.geometry.x, "max_wind_kt": float(p.get("MAXWIND") or 0), "label": p.get("DVLBL")})
    return out
