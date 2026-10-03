"""NASA FIRMS active fire detections (VIIRS S-NPP NRT, last 24 h) (spec §5)."""

from __future__ import annotations

import csv
import io

import httpx

from app.config import RegionConfig, settings

PAD_DEG = 0.5  # ~50 km around the region


async def fetch_fires(client: httpx.AsyncClient, region: RegionConfig) -> list[dict]:
    key = settings().firms_map_key
    if not key:
        raise RuntimeError("FIRMS_MAP_KEY not configured")
    w, s, e, n = region.bbox
    area = f"{w - PAD_DEG:.3f},{s - PAD_DEG:.3f},{e + PAD_DEG:.3f},{n + PAD_DEG:.3f}"
    r = await client.get(f"{settings().firms_base}/{key}/VIIRS_SNPP_NRT/{area}/1")
    r.raise_for_status()
    if r.text.lower().startswith("invalid"):
        raise RuntimeError(r.text.strip()[:200])
    out = []
    for row in csv.DictReader(io.StringIO(r.text)):
        try:
            out.append({
                "lat": float(row["latitude"]), "lon": float(row["longitude"]),
                "acq": f"{row['acq_date']}T{row['acq_time'].zfill(4)[:2]}:{row['acq_time'].zfill(4)[2:]}:00+00:00",
                "confidence": row.get("confidence"), "frp": float(row.get("frp") or 0),
                "daynight": row.get("daynight"),
            })
        except (KeyError, ValueError):
            continue
    return out
