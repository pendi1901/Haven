"""DEM download (once) and local sampling (spec §5 rule: never call an elevation
service per point).

Source: USGS 3DEP 1 m lidar DEM tiles (cloud-optimized GeoTIFFs on the USGS S3
bucket), read through their internal 4 m overviews and mosaicked into one GeoTIFF
for the region bbox. Native CRS is NAD83 / UTM 17N; elevations are meters NAVD88,
the same vertical datum NWPS uses for gauge datums.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import httpx
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import reproject

from app.config import WORK_CRS, RegionConfig

log = logging.getLogger(__name__)

TNM_PRODUCTS = "https://tnmaccess.nationalmap.gov/api/v1/products"
DEM_RES_M = 4.0


def dem_path(region: RegionConfig) -> Path:
    return region.data_dir / "dem" / "dem_4m.tif"


def download_dem(region: RegionConfig, force: bool = False) -> Path:
    out = dem_path(region)
    if out.exists() and not force:
        log.info("DEM cached at %s", out)
        return out
    out.parent.mkdir(parents=True, exist_ok=True)

    w, s, e, n = region.bbox

    def products(dataset: str) -> list[dict]:
        resp = httpx.get(TNM_PRODUCTS, params={"bbox": f"{w},{s},{e},{n}", "datasets": dataset,
                                               "prodFormats": "GeoTIFF", "max": 200}, timeout=60)
        resp.raise_for_status()
        return resp.json()["items"]

    # Prefer 1 m lidar; fall back to the 1/3 arc-second (~10 m) seamless DEM where
    # 1 m tiles are not staged (e.g. parts of Wake County, NC).
    items = products("Digital Elevation Model (DEM) 1 meter")
    source = "USGS 3DEP 1 m lidar DEM"
    if not items:
        items = products("National Elevation Dataset (NED) 1/3 arc-second")
        source = "USGS 3DEP 1/3 arc-second DEM (~10 m)"
    # Keep the newest tile per grid cell (projects and vintages overlap).
    by_cell: dict[str, dict] = {}
    for it in items:
        parts = it["title"].split(" ")
        cell = next((x for x in parts if (x.startswith("x") and "y" in x) or (x.startswith("n") and "w" in x)), it["title"])
        if cell not in by_cell or (it.get("publicationDate") or "") > (by_cell[cell].get("publicationDate") or ""):
            by_cell[cell] = it
    urls = [it["downloadURL"] for it in by_cell.values()]
    if not urls:
        raise RuntimeError("No 3DEP DEM tiles found for region bbox")
    log.info("DEM: %d tiles (%s)", len(urls), source)

    to_work = Transformer.from_crs("EPSG:4326", WORK_CRS, always_xy=True)
    xs, ys = to_work.transform([w, e, w, e], [s, s, n, n])
    x0, x1 = np.floor(min(xs) / DEM_RES_M) * DEM_RES_M, np.ceil(max(xs) / DEM_RES_M) * DEM_RES_M
    y0, y1 = np.floor(min(ys) / DEM_RES_M) * DEM_RES_M, np.ceil(max(ys) / DEM_RES_M) * DEM_RES_M
    width, height = int((x1 - x0) / DEM_RES_M), int((y1 - y0) / DEM_RES_M)
    dst_transform = from_origin(x0, y1, DEM_RES_M, DEM_RES_M)
    mosaic = np.full((height, width), np.nan, dtype=np.float32)

    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif")
    with rasterio.Env(**env):
        for i, url in enumerate(urls, 1):
            log.info("DEM tile %d/%d %s", i, len(urls), url.rsplit("/", 1)[-1])
            with rasterio.open(f"/vsicurl/{url}") as src:
                if src.crs.is_geographic:
                    # Seamless 1/3" tiles: warp just our window onto the 4 m grid.
                    with WarpedVRT(src, crs=WORK_CRS, transform=dst_transform, width=width, height=height,
                                   resampling=Resampling.bilinear, src_nodata=src.nodata, nodata=np.nan) as vrt:
                        tile = vrt.read(1).astype(np.float32)
                    tile[tile < -1000] = np.nan
                    fill = np.isnan(mosaic) & ~np.isnan(tile)
                    mosaic[fill] = tile[fill]
                    continue
                factor = int(round(DEM_RES_M / src.res[0]))
                data = src.read(
                    1,
                    out_shape=(src.height // factor, src.width // factor),
                    resampling=Resampling.average,
                    masked=True,
                ).filled(np.nan).astype(np.float32)
                src_transform = src.transform * src.transform.scale(
                    src.width / data.shape[1], src.height / data.shape[0]
                )
                tile = np.full_like(mosaic, np.nan)
                reproject(
                    data,
                    tile,
                    src_transform=src_transform,
                    src_crs=src.crs,
                    src_nodata=np.nan,
                    dst_transform=dst_transform,
                    dst_crs=WORK_CRS,
                    dst_nodata=np.nan,
                    resampling=Resampling.bilinear,
                )
                fill = np.isnan(mosaic) & ~np.isnan(tile)
                mosaic[fill] = tile[fill]

    profile = dict(
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs=WORK_CRS,
        transform=dst_transform,
        nodata=np.nan,
        compress="deflate",
        predictor=3,
        tiled=True,
    )
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(mosaic, 1)
        dst.update_tags(SOURCE=source)
    log.info("DEM written %s (%dx%d, %.1f%% filled)", out, width, height,
             100 * np.isfinite(mosaic).mean())
    return out


class Dem:
    """In-memory DEM with vectorized bilinear sampling in the working CRS."""

    def __init__(self, path: Path):
        with rasterio.open(path) as ds:
            self.data = ds.read(1).astype(np.float32)
            self.transform = ds.transform
            self.crs = ds.crs
            self.source = ds.tags().get("SOURCE", "USGS 3DEP 1 m lidar DEM")
        self.res = self.transform.a
        self.x0 = self.transform.c
        self.y0 = self.transform.f
        self.height, self.width = self.data.shape

    def sample(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Bilinear elevation (m NAVD88) at working-CRS coordinates. NaN outside."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        col = (x - self.x0) / self.res - 0.5
        row = (self.y0 - y) / self.res - 0.5
        c0 = np.floor(col).astype(np.int64)
        r0 = np.floor(row).astype(np.int64)
        fc = col - c0
        fr = row - r0
        ok = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < self.width) & (r0 + 1 < self.height)
        out = np.full(x.shape, np.nan, dtype=np.float64)
        c0o, r0o, fco, fro = c0[ok], r0[ok], fc[ok], fr[ok]
        d = self.data
        v = (
            d[r0o, c0o] * (1 - fco) * (1 - fro)
            + d[r0o, c0o + 1] * fco * (1 - fro)
            + d[r0o + 1, c0o] * (1 - fco) * fro
            + d[r0o + 1, c0o + 1] * fco * fro
        )
        out[ok] = v
        return out

    def sample_one(self, x: float, y: float) -> float | None:
        v = float(self.sample(np.array([x]), np.array([y]))[0])
        return None if np.isnan(v) else v


@lru_cache(maxsize=4)
def load_dem(path: Path) -> Dem:
    return Dem(path)
