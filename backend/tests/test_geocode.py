from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_geocode as geocode

ROW = {"display_name": "Raleigh, Wake County, North Carolina, United States", "lat": "35.7796", "lon": "-78.6382"}


@pytest.fixture
def client(monkeypatch):
    geocode._cache.clear()
    geocode._last = 0.0
    state = {"handler": lambda req: httpx.Response(200, json=[ROW])}
    monkeypatch.setattr(geocode, "make_client",
                        lambda timeout=15: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: state["handler"](r))))
    app = FastAPI()
    app.include_router(geocode.router)
    c = TestClient(app, raise_server_exceptions=False)
    c.set_handler = lambda h: state.__setitem__("handler", h)
    yield c
    geocode._cache.clear()


def test_normal_result(client):
    r = client.get("/api/geocode", params={"q": "Raleigh, NC"})
    assert r.status_code == 200
    body = r.json()
    assert body == [{"label": "Raleigh, Wake County, North Carolina", "lat": 35.7796, "lon": -78.6382}]


def test_duplicates_removed(client):
    client.set_handler(lambda req: httpx.Response(200, json=[ROW, ROW]))
    r = client.get("/api/geocode", params={"q": "Raleigh"})
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_network_error_is_503(client):
    def boom(req):
        raise httpx.ConnectTimeout("timed out", request=req)
    client.set_handler(boom)
    r = client.get("/api/geocode", params={"q": "Raleigh"})
    assert r.status_code == 503
    assert "unavailable" in r.json()["detail"]


def test_rate_limited_is_busy(client):
    client.set_handler(lambda req: httpx.Response(429))
    r = client.get("/api/geocode", params={"q": "Raleigh"})
    assert r.status_code == 503
    assert "busy" in r.json()["detail"]


def test_non_json_is_502(client):
    client.set_handler(lambda req: httpx.Response(200, text="<html>"))
    r = client.get("/api/geocode", params={"q": "Raleigh"})
    assert r.status_code == 502
    assert "unavailable" in r.json()["detail"]


def test_failures_not_cached(client, monkeypatch):
    monkeypatch.setattr(geocode.asyncio, "sleep", _no_sleep)
    client.set_handler(lambda req: httpx.Response(500))
    assert client.get("/api/geocode", params={"q": "Raleigh"}).status_code == 502
    client.set_handler(lambda req: httpx.Response(200, json=[ROW]))
    r = client.get("/api/geocode", params={"q": "Raleigh"})
    assert r.status_code == 200
    assert len(r.json()) == 1


async def _no_sleep(_s):
    return None
