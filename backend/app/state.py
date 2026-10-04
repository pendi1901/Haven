"""Application context: data mode, time, sources, hazard modules, and the cached
"world" (everything known at time t) that endpoints read from."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import geopandas as gpd
import shapely

from app.config import POLL_SECONDS, REGIONS, WORK_CRS, RegionConfig, region as current_region, settings
from app.geo.elevation import Dem, dem_path, load_dem
from app.geo.null import NullDem, NullImpact, NullRouter
from app.geo.road_impact import ImpactResult, RoadImpact
from app.geo.routing import Router
from app.hazards.air_quality import AirQuality
from app.hazards.alerts import Alerts
from app.hazards.earthquake import Earthquake
from app.hazards.heat import Heat
from app.hazards.hurricane import Hurricane
from app.hazards.river_flood import RiverFlood
from app.hazards.uv import UV
from app.hazards.wildfire import Wildfire
from app.hazards.wind import Wind
from app.models import GaugeStatus, HazardZone
from app.replay.loader import ReplayData
from app.sources.base import SourceCache, make_client, poll_forever, utcnow
from app.sources.locate import point_key, point_region
from app.sources.ncdot import fetch_closures
from app.sources.nwps import load_gauge_meta
from app.sources.shelters import fetch_open_shelters, load_osm_pois

log = logging.getLogger(__name__)


@dataclass
class World:
    t: datetime
    gauges: list[GaugeStatus]
    impact: ImpactResult
    alerts: list[HazardZone]
    zones: list[HazardZone] = field(default_factory=list)
    shelters: list[dict] = field(default_factory=list)
    closures: list[dict] = field(default_factory=list)
    version: int = 0
    zones_ready: bool = False


class Haven:
    def __init__(self, region: RegionConfig | None = None, mode: str | None = None):
        self.settings = settings()
        self.region = region or current_region()
        self.mode = mode or self.settings.data_mode
        self.cache = SourceCache(self.region.data_dir / "cache")
        self.replay = ReplayData(self.region) if self.mode == "replay" else None
        if self.mode == "replay" and not (self.replay and self.replay.available):
            raise RuntimeError(f"Replay data missing for {self.region.key}: run `python -m app.prepare`")
        self.gauge_meta = load_gauge_meta(self.region)
        self.overlay = self.region.has_overlay
        if self.overlay:
            self.dem: Dem | NullDem = load_dem(dem_path(self.region))
            self.impact: RoadImpact | NullImpact = RoadImpact(self.region)
            rivers = gpd.read_file(self.region.data_dir / "osm" / "rivers.geojson").to_crs(WORK_CRS)
            self.rivers_work = shapely.union_all(rivers.geometry.values)
            self.pois = load_osm_pois(self.region)
            self.router: Router | NullRouter = Router(self)
        else:
            # Live anywhere: point sources work; the road overlay needs prepared data.
            self.dem, self.impact, self.router = NullDem(), NullImpact(), NullRouter()
            self.rivers_work = shapely.GeometryCollection()
            self.pois = []
        self.modules = {
            "river_flood": RiverFlood(self),
            "alerts": Alerts(self),
            "air_quality": AirQuality(self),
            "heat": Heat(self),
            "uv": UV(self),
            "wind": Wind(self),
            "wildfire": Wildfire(self),
            "earthquake": Earthquake(self),
            "hurricane": Hurricane(self),
        }
        self.client = None
        self.hub: "Hub | None" = None
        self.version = 0
        self._worlds: OrderedDict[tuple, World] = OrderedDict()
        self._lock = threading.Lock()
        self._key_locks: dict[tuple, threading.RLock] = {}
        self._tasks: list[asyncio.Task] = []
        self.subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.cache.listeners.append(self._on_source_change)

    # -- time -----------------------------------------------------------------

    def now(self) -> datetime:
        return utcnow()

    def resolve_t(self, t: datetime | None) -> datetime:
        """Live: always now (spec §10). Replay: clamp to the window, floor to the step."""
        if self.mode == "live":
            return self.now().replace(second=0, microsecond=0)
        start, end, step = self.replay.window()
        if t is None:
            t = start + (end - start) * 0.5
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        t = min(max(t.astimezone(timezone.utc), start), end)
        steps = int((t - start).total_seconds() // (step * 60))
        return start + timedelta(minutes=step * steps)

    # -- world ------------------------------------------------------------------

    def world(self, t: datetime) -> World:
        key = (t, self.version)
        with self._lock:
            w = self._worlds.get(key)
            if w is not None and w.zones_ready:
                self._worlds.move_to_end(key)
                return w
            key_lock = self._key_locks.setdefault(key, threading.RLock())
        # One lock per timestep: requests only wait for the timestep they need, never
        # for the replay warm-up working on other timesteps. Reentrant because zone
        # builders call world(t) again on the same thread.
        with key_lock:
            with self._lock:
                w = self._worlds.get(key)
            if w is not None and w.zones_ready:
                return w
            if w is None:
                w = self._build_world(t)
                with self._lock:
                    self._worlds[key] = w
                    while len(self._worlds) > 800:
                        old, _ = self._worlds.popitem(last=False)
                        self._key_locks.pop(old, None)
            else:
                return w  # reentrant call while zones are being built
            zones: list[HazardZone] = []
            for name, m in self.modules.items():
                if name == "alerts":
                    continue
                try:
                    zones.extend(m.zones(t))
                except Exception as ex:  # noqa: BLE001 - one module must not break the app
                    log.warning("zones from %s failed: %s", name, ex)
            w.zones = w.alerts + zones
            w.zones_ready = True
            return w

    def _build_world(self, t: datetime) -> World:
        river: RiverFlood = self.modules["river_flood"]  # type: ignore[assignment]
        gauges = river.gauges(t)
        levels = river.levels(t, gauges)
        closures = [] if self.mode == "replay" else (self.cache.get("ncdot").value or [])
        closure_keys = self._closure_edges(closures)
        impact = self.impact.evaluate(t, levels, closure_keys)
        alerts = self.modules["alerts"].zones(t)
        shelters = [] if self.mode == "replay" else (self.cache.get("shelters").value or [])
        return World(t=t, gauges=gauges, impact=impact, alerts=alerts, shelters=shelters,
                     closures=closures, version=self.version)

    def _closure_edges(self, closures: list[dict]) -> set[tuple[str, str]]:
        if not closures:
            return set()
        from shapely.geometry import shape
        from shapely.ops import transform as shp_transform

        tree = self.impact.edge_tree()
        geoms = self.impact._edge_geoms()
        out = set()
        for c in closures:
            g = shp_transform(lambda x, y, z=None: self.impact.to_work.transform(x, y), shape(c["geometry"]))
            for j in tree.query(g, predicate="dwithin", distance=30.0):
                e = self.impact.edges[geoms[j][0]]
                out.add((e["graph"], e["eid"]))
        return out

    def warm_replay_cache(self) -> None:
        """Precompute every replay timestep so the slider scrubs instantly (spec §11)."""
        if self.mode != "replay":
            return
        start, end, step = self.replay.window()
        t = start
        n = 0
        while t <= end:
            try:
                self.world(t)
            except Exception as ex:  # noqa: BLE001
                log.warning("warm %s failed: %s", t, ex)
            t += timedelta(minutes=step)
            n += 1
            time.sleep(0.01)  # let request threads in
        log.info("replay cache warm: %d timesteps", n)

    # -- live polling -------------------------------------------------------------

    def _on_source_change(self, name: str) -> None:
        self.version += 1
        if self._loop is None:
            return
        for q in list(self.subscribers):
            self._loop.call_soon_threadsafe(q.put_nowait, {"event": "state_updated", "source": name,
                                                           "version": self.version})

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self.mode != "live":
            return
        self.cache.restore()
        self.client = make_client()
        m = self.modules
        jobs = [
            ("river_flood", m["river_flood"].refresh, POLL_SECONDS["nwps"]),
            ("nws_alerts", m["alerts"].refresh, POLL_SECONDS["nws_alerts"]),
            ("nws_forecast", m["heat"].refresh, POLL_SECONDS["nws_forecast"]),
            ("airnow", m["air_quality"].refresh, POLL_SECONDS["airnow"]),
            ("epa_uv", m["uv"].refresh, POLL_SECONDS["epa_uv"]),
            ("firms", m["wildfire"].refresh, POLL_SECONDS["firms"]),
            ("quakes", m["earthquake"].refresh, POLL_SECONDS["quakes"]),
            ("nhc", m["hurricane"].refresh, POLL_SECONDS["nhc"]),
        ]
        for i, (name, fn, interval) in enumerate(jobs):
            # prime() fetches everything once; pollers take over after one interval.
            self._tasks.append(asyncio.create_task(self._loop_refresh(name, fn, interval, interval + i * 0.7)))
        self._tasks.append(asyncio.create_task(poll_forever(
            self.cache, "shelters", lambda: fetch_open_shelters(self.client, self.region), POLL_SECONDS["shelters"],
            POLL_SECONDS["shelters"])))
        self._tasks.append(asyncio.create_task(poll_forever(
            self.cache, "ncdot", lambda: fetch_closures(self.client), POLL_SECONDS["ncdot"], 4)))

    async def prime(self, timeout: float = 20.0) -> None:
        """Fetch every live source once so the first answer is not empty."""
        if self.mode != "live":
            return
        now = self.now()
        jobs = [m.refresh(now) for m in self.modules.values()]
        jobs.append(self._prime_shelters())
        try:
            await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), timeout)
        except asyncio.TimeoutError:
            log.warning("priming %s timed out; continuing with what arrived", self.region.key)

    async def _prime_shelters(self) -> None:
        try:
            self.cache.put("shelters", await fetch_open_shelters(self.client, self.region))
        except Exception as ex:  # noqa: BLE001
            self.cache.fail("shelters", f"{type(ex).__name__}: {ex}")

    async def _loop_refresh(self, name: str, fn, interval: int, delay: float) -> None:
        await asyncio.sleep(delay)
        while True:
            try:
                await fn(self.now())
            except asyncio.CancelledError:
                raise
            except Exception as ex:  # noqa: BLE001
                log.warning("refresh %s failed: %s", name, ex)
            await asyncio.sleep(interval)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self.client:
            await self.client.aclose()

    def source_status(self) -> dict:
        if self.mode == "replay" and self.region.replay_simulated:
            return {"replay": {"event": self.region.replay_event, "simulated": True, "archives": [
                "Simulated gauge readings, river forecasts and alerts (demo scenario, not official data)"]}}
        if self.mode == "replay":
            return {"replay": {"event": self.region.replay_event, "archives": [
                "USGS Water Data API (15-min gage height)",
                "NWS LMRFC river forecasts (RVF), IEM archive",
                "NWS warnings and watches (VTEC), IEM archive",
                "NHC forecast cones, NHC GIS archive"]}}
        names = [f"nwps:{g.lid}" for g in self.region.gauges] + [
            "nws_alerts", "nws_forecast", "airnow", "purpleair", "epa_uv", "firms", "quakes", "nhc", "shelters", "ncdot"]
        return {n: self.cache.get(n).status() for n in names}


class Hub:
    """One server, many contexts: the Helene replay, prepared live regions (with the
    road-flood overlay), and live point regions built around wherever users are."""

    MAX_POINT_CONTEXTS = 12

    def __init__(self) -> None:
        self.settings = settings()
        self._havens: OrderedDict[tuple[str, str], Haven] = OrderedDict()
        self._lock = asyncio.Lock()
        self._client = None
        self._listeners: set[asyncio.Queue] = set()

    def default_mode(self) -> str:
        return self.settings.data_mode

    def replay_available(self) -> bool:
        return bool(self.replay_regions())

    def replay_regions(self) -> list[RegionConfig]:
        """Prepared replay scenarios, the default (REPLAY_REGION) first."""
        regions = [r for r in REGIONS.values() if r.has_replay]
        return sorted(regions, key=lambda r: r.key != self.settings.replay_region)

    async def get(self, mode: str | None = None, lat: float | None = None, lon: float | None = None,
                  scenario: str | None = None) -> Haven:
        mode = mode or self.default_mode()
        if mode == "replay":
            region = REGIONS.get(scenario or self.settings.replay_region)
            if region is None:
                raise LookupError(f"Unknown replay scenario {scenario!r}.")
            if not region.has_replay:
                raise LookupError("Replay data is not prepared. Run `python -m app.prepare`"
                                  + (" or `python -m app.replay.simulate`." if region.replay_simulated else "."))
        elif lat is None or lon is None:
            region = REGIONS.get(self.settings.region)
            if region is None or not region.has_overlay:
                region = next((r for r in REGIONS.values() if r.has_overlay), None)
            if region is None:
                raise LookupError("No location given and no prepared region.")
        else:
            region = next((r for r in REGIONS.values() if r.has_overlay and r.contains(lat, lon)), None)
            if region is None:
                key = point_key(lat, lon)
                h = self._havens.get(("live", key))
                if h is not None:
                    self._havens.move_to_end(("live", key))
                    return h
                region = await point_region(await self.client(), lat, lon)
        key = (mode, region.key)
        h = self._havens.get(key)
        if h is not None:
            self._havens.move_to_end(key)
            return h
        async with self._lock:
            h = self._havens.get(key)
            if h is not None:
                return h
            h = await asyncio.to_thread(Haven, region, mode)
            h.hub = self
            await h.start()
            await h.prime()
            if h.overlay:
                def warm():
                    h.router.graph("walk")
                    h.router.graph("drive")
                    if mode == "replay" and self.settings.warm_replay_cache:
                        h.warm_replay_cache()
                threading.Thread(target=warm, daemon=True).start()
            self._havens[key] = h
            await self._evict()
            log.info("context ready: %s %s (%s, overlay=%s)", mode, region.key, region.name, h.overlay)
            return h

    async def _evict(self) -> None:
        points = [k for k in self._havens if self._havens[k].region.point]
        while len(points) > self.MAX_POINT_CONTEXTS:
            old = points.pop(0)
            await self._havens.pop(old).stop()

    async def client(self):
        if self._client is None:
            self._client = make_client()
        return self._client

    def set(self, h: Haven) -> None:
        """Tests: install a prebuilt context."""
        h.hub = self
        self._havens[(h.mode, h.region.key)] = h

    async def stop(self) -> None:
        for h in self._havens.values():
            await h.stop()
        if self._client:
            await self._client.aclose()


_hub: Hub | None = None


def get_hub() -> Hub:
    global _hub
    if _hub is None:
        _hub = Hub()
    return _hub


def set_haven(h: Haven | None) -> None:
    """Tests: make `h` the context for its mode."""
    if h is not None:
        get_hub().set(h)
