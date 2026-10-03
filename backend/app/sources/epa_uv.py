"""EPA Envirofacts hourly UV index forecast by ZIP (spec §5)."""

from __future__ import annotations

from datetime import datetime

import httpx

from app.config import RegionConfig, settings


async def fetch_uv(client: httpx.AsyncClient, region: RegionConfig) -> list[dict]:
    r = await client.get(f"{settings().epa_uv_base}/getEnvirofactsUVHOURLY/ZIP/{region.uv_zip}/JSON")
    r.raise_for_status()
    out = []
    for row in r.json():
        # "Oct/03/2026 07 AM" in the ZIP's local time.
        local = datetime.strptime(row["DATE_TIME"], "%b/%d/%Y %I %p")
        out.append({"local_time": local.isoformat(), "uv": row.get("UV_VALUE")})
    return out
