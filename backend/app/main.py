"""FastAPI app: routers, startup, and (optionally) the built frontend."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes_geocode, routes_responder, routes_route, routes_state, routes_user, stream
from app.api.deps import ScenarioMiddleware
from app.config import REGIONS, REPO_DIR, settings
from app.state import get_hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("haven")


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub = get_hub()
    log.info("Haven starting: default mode=%s", hub.default_mode())
    try:
        # Warm the default context so the first visitor doesn't wait.
        await hub.get(hub.default_mode())
    except LookupError as ex:
        log.warning("no default context: %s", ex)
    # And every prepared live region in the background (road graphs take seconds).
    async def warm_regions():
        for r in REGIONS.values():
            if r.has_overlay:
                try:
                    await hub.get("live", *r.center)
                except Exception as ex:  # noqa: BLE001
                    log.warning("warming %s failed: %s", r.key, ex)
    warm_task = asyncio.create_task(warm_regions())
    yield
    warm_task.cancel()
    await hub.stop()


app = FastAPI(title="Haven", version="0.1.0", lifespan=lifespan,
              description="Decision support layered on official NOAA/NWS/USGS/EPA/NASA data.")
app.add_middleware(ScenarioMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings().cors_origins.split(",")],
                   allow_methods=["*"], allow_headers=["*"])
for r in (routes_state, routes_user, routes_route, routes_responder, routes_geocode, stream):
    app.include_router(r.router)


@app.get("/api/health")
def health():
    return {"ok": True, "default_mode": get_hub().default_mode()}


DIST = REPO_DIR / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
