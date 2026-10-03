"""FastAPI app: routers, startup, and (optionally) the built frontend."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes_responder, routes_route, routes_state, routes_user, stream
from app.config import REPO_DIR, settings
from app.state import get_haven

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("haven")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ctx = get_haven()
    log.info("Haven starting: region=%s mode=%s", ctx.region.key, ctx.mode)
    await ctx.start()
    # Load routing graphs and precompute replay timesteps off the event loop.
    def warm():
        ctx.router.graph("walk")
        ctx.router.graph("drive")
        if settings().warm_replay_cache:
            ctx.warm_replay_cache()
    threading.Thread(target=warm, daemon=True).start()
    yield
    await ctx.stop()


app = FastAPI(title="Haven", version="0.1.0", lifespan=lifespan,
              description="Decision support layered on official NOAA/NWS/USGS/EPA/NASA data.")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings().cors_origins.split(",")],
                   allow_methods=["*"], allow_headers=["*"])
for r in (routes_state, routes_user, routes_route, routes_responder, stream):
    app.include_router(r.router)


@app.get("/api/health")
def health():
    ctx = get_haven()
    return {"ok": True, "mode": ctx.mode, "region": ctx.region.key}


DIST = REPO_DIR / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
