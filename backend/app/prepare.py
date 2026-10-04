"""One-time data preparation. Every step is cached and idempotent.

    python -m app.prepare                 # everything for $REGION
    python -m app.prepare --steps dem osm # selected steps
    python -m app.prepare --force rvf     # re-download one step
"""

from __future__ import annotations

import argparse
import logging
import time

from app.config import region as current_region
from app.sources.base import sync_client

STEPS = ["gauges", "osm", "dem", "replay", "roadindex", "census"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--steps", nargs="*", default=STEPS, choices=STEPS)
    ap.add_argument("--force", nargs="*", default=[], help="steps to re-download")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("prepare")
    region = current_region()
    log.info("Preparing data for region %s in %s", region.key, region.data_dir)
    region.data_dir.mkdir(parents=True, exist_ok=True)

    with sync_client() as client:
        for step in args.steps:
            t0 = time.time()
            force = step in args.force
            log.info("== %s", step)
            if step == "gauges":
                from app.sources.nwps import snapshot_gauge_meta
                snapshot_gauge_meta(region, client, force)
            elif step == "osm":
                from app.geo.roads import download_graphs, download_pois, download_rivers
                download_rivers(region, force)
                download_pois(region, force)
                download_graphs(region, force)
            elif step == "dem":
                from app.geo.elevation import download_dem
                download_dem(region, force)
            elif step == "replay":
                if region.replay_start is None:
                    log.info("region has no replay window; skipping")
                    continue
                if region.replay_simulated:
                    log.info("replay here is simulated: run `python -m app.replay.simulate` instead")
                    continue
                from app.replay.loader import fetch_alerts, fetch_nhc, fetch_rvf, fetch_usgs
                fetch_usgs(region, client, force)
                fetch_rvf(region, client, force)
                fetch_alerts(region, client, force)
                fetch_nhc(region, client, force)
            elif step == "roadindex":
                from app.geo.road_impact import build_road_index
                build_road_index(region, force)
            elif step == "census":
                from app.geo.census import download_census
                try:
                    download_census(region, client, force)
                except Exception as ex:  # optional (responder view)
                    log.warning("census step failed (responder view will be limited): %s", ex)
            log.info("== %s done in %.1fs", step, time.time() - t0)


if __name__ == "__main__":
    main()
