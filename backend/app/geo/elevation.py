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
    resp = httpx.get(
        TNM_PRODUCTS,
        params={
            "bbox": f"{w},{s},{e},{n}",
            "datasets": "Digital Elevation Model (DEM) 1 meter",
            "prodFormats": "GeoTIFF",
            "max": 200,
        },
        timeout=60,
    )
    resp.raise_for_status()
    items = resp.json()["items"]
    # Keep the newest tile per grid cell (projects can overlap).
    by_cell: dict[str, dict] = {}
    for it in items:
        cell = it["title"].split(" ")[4] if len(it["title"].split(" ")) > 4 else it["title"]
        if cell not in by_cell or it["publicationDate"] > by_cell[cell]["publicationDate"]:
            by_cell[cell] = it
    urls = [it["downloadURL"] for it in by_cell.values()]
    if not urls:
        raise RuntimeError("No 1 m 3DEP tiles found for region bbox")
    log.info("DEM: %d 1 m tiles", len(urls))

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
