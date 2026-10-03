"""Shared HTTP client and the poll cache (spec §7.1).

Every source stores its latest good value with the fetch time, writes a JSON
snapshot to disk, and on failure keeps the last good value marked stale. Nothing
here raises into the app.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from app.config import POLL_SECONDS, STALE_AFTER_INTERVALS, settings

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_client(timeout: float = 30.0) -> httpx.AsyncClient:
    # Some government endpoints resolve to IPv6 addresses that are unreachable on
    # some networks; binding to 0.0.0.0 forces IPv4.
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=2)
    return httpx.AsyncClient(
        timeout=timeout,
        transport=transport,
        headers={"User-Agent": settings().nws_user_agent, "Accept": "application/json, application/geo+json, */*"},
        follow_redirects=True,
    )


def sync_client(timeout: float = 60.0) -> httpx.Client:
    transport = httpx.HTTPTransport(local_address="0.0.0.0", retries=2)
    return httpx.Client(
        timeout=timeout,
        transport=transport,
        headers={"User-Agent": settings().nws_user_agent},
        follow_redirects=True,
    )


@dataclass
class Entry:
    value: Any = None
    fetched_at: datetime | None = None
    error: str | None = None
    error_at: datetime | None = None
    interval_s: int = 300
    configured: bool = True

    @property
    def stale(self) -> bool:
        if self.fetched_at is None:
            return True
        return utcnow() - self.fetched_at > timedelta(seconds=self.interval_s * STALE_AFTER_INTERVALS)

    def status(self) -> dict:
        return {
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "stale": self.stale,
            "error": self.error,
            "error_at": self.error_at.isoformat() if self.error_at else None,
            "configured": self.configured,
        }


@dataclass
class SourceCache:
    snapshot_dir: Path
    entries: dict[str, Entry] = field(default_factory=dict)
    listeners: list[Callable[[str], None]] = field(default_factory=list)

    def get(self, name: str) -> Entry:
        return self.entries.setdefault(name, Entry(interval_s=POLL_SECONDS.get(name.split(":")[0], 300)))

    def put(self, name: str, value: Any) -> None:
        e = self.get(name)
        changed = json.dumps(value, default=str, sort_keys=True) != json.dumps(e.value, default=str, sort_keys=True)
        e.value = value
        e.fetched_at = utcnow()
        e.error = None
        self._snapshot(name, e)
        if changed:
            for fn in self.listeners:
                fn(name)

    def fail(self, name: str, err: str) -> None:
        e = self.get(name)
        e.error = err
        e.error_at = utcnow()
        log.warning("source %s failed: %s", name, err)

    def _snapshot(self, name: str, e: Entry) -> None:
        try:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
            path = self.snapshot_dir / f"{name.replace(':', '__')}.json"
            path.write_text(json.dumps({"fetched_at": e.fetched_at.isoformat(), "value": e.value}, default=str))
        except OSError as ex:  # never crash on disk issues
            log.warning("snapshot write failed for %s: %s", name, ex)

    def restore(self) -> None:
        """Load last snapshots so a restart shows last-known values (marked stale)."""
        if not self.snapshot_dir.exists():
            return
        for path in self.snapshot_dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text())
                e = self.get(path.stem.replace("__", ":"))
                e.value = raw["value"]
                e.fetched_at = datetime.fromisoformat(raw["fetched_at"])
            except (OSError, ValueError, KeyError):
                continue


async def poll_forever(
    cache: SourceCache,
    name: str,
    fetch: Callable[[], Awaitable[Any]],
    interval_s: int,
    jitter_s: float = 0.0,
) -> None:
    cache.get(name).interval_s = interval_s
    if jitter_s:
        await asyncio.sleep(jitter_s)
    while True:
        try:
            value = await fetch()
            cache.put(name, value)
        except asyncio.CancelledError:
            raise
        except Exception as ex:  # noqa: BLE001 - a poller must never die
            cache.fail(name, f"{type(ex).__name__}: {ex}"[:300])
        await asyncio.sleep(interval_s)
