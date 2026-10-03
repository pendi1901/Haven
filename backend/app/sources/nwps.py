"""NOAA National Water Prediction Service: gauge metadata, flood categories,
observed stage and the official river forecast (spec §5, §7.2)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx

from app.config import RegionConfig, settings


def parse_gauge_meta(raw: dict) -> dict:
    cats = (raw.get("flood") or {}).get("categories") or {}
    thresholds = {
        k: float(v["stage"])
        for k, v in cats.items()
        if isinstance(v, dict) and v.get("stage") not in (None, -9999)
    }
    datum = None
    for d in ((raw.get("datums") or {}).get("vertical") or {}).get("value") or []:
        if d.get("abbrev") == "NAVD88" and d.get("value") not in (None, -9999):
            datum = float(d["value"])
    crests = ((raw.get("flood") or {}).get("crests") or {}).get("historic") or []
    record = max((c["stage"] for c in crests if c.get("stage")), default=None)
    return {
        "lid": raw["lid"],
        "usgs_id": raw.get("usgsId") or None,
        "name": raw.get("name"),
        "lat": float(raw["latitude"]),
        "lon": float(raw["longitude"]),
        "thresholds_ft": thresholds,
        "datum_navd88_ft": datum,
        "record_crest_ft": record,
        "reach_id": raw.get("reachId"),
        "has_official_forecast": bool((raw.get("pedts") or {}).get("forecast")),
    }


def parse_stageflow(raw: dict) -> dict:
    def pts(block: dict | None) -> list[dict]:
        if not block:
            return []
        out = []
        for p in block.get("data") or []:
            v = p.get("primary")
            if v is None or v == -999 or v <= -999:
                continue
            out.append({"t": p["validTime"], "stage_ft": float(v)})
        return out

    obs = raw.get("observed") or {}
    fc = raw.get("forecast") or {}
    return {
        "observed": pts(obs),
        "observed_issued": obs.get("issuedTime"),
        "forecast": pts(fc),
        "forecast_issued": fc.get("issuedTime"),
    }


async def fetch_gauge(client: httpx.AsyncClient, lid: str) -> dict:
    base = settings().nwps_base
    meta_r = await client.get(f"{base}/gauges/{lid}")
    meta_r.raise_for_status()
    sf_r = await client.get(f"{base}/gauges/{lid}/stageflow")
    sf_r.raise_for_status()
    meta = parse_gauge_meta(meta_r.json())
    sf = parse_stageflow(sf_r.json())
    return {"meta": meta, **sf, "fetched_at": datetime.utcnow().isoformat() + "Z"}


async def fetch_reach_streamflow(client: httpx.AsyncClient, reach_id: str) -> dict:
    """National Water Model short-range flow for a reach (display only, spec §5)."""
    r = await client.get(f"{settings().nwps_base}/reaches/{reach_id}/streamflow", params={"series": "short_range"})
    r.raise_for_status()
    raw = r.json()
    series = ((raw.get("shortRange") or {}).get("series") or {})
    data = series.get("data") or []
    return {
        "reach_id": reach_id,
        "units": series.get("units"),
        "points": [{"t": p["validTime"], "flow": p["flow"]} for p in data if p.get("flow") is not None],
    }


def gauge_meta_path(region: RegionConfig, lid: str) -> Path:
    return region.data_dir / "gauges" / f"{lid}.json"


def snapshot_gauge_meta(region: RegionConfig, client: httpx.Client, force: bool = False) -> None:
    """Cache static gauge metadata (thresholds, datum) for offline / replay use."""
    for g in region.gauges:
        path = gauge_meta_path(region, g.lid)
        if path.exists() and not force:
            continue
        r = client.get(f"{settings().nwps_base}/gauges/{g.lid}")
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(parse_gauge_meta(r.json()), indent=1))


def load_gauge_meta(region: RegionConfig) -> dict[str, dict]:
    out = {}
    for g in region.gauges:
        path = gauge_meta_path(region, g.lid)
        if path.exists():
            out[g.lid] = json.loads(path.read_text())
    return out
