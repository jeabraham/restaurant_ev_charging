"""Live integration tests for Geoapify pass-through endpoints.

These tests call the real Geoapify APIs through this service and are skipped
unless a real GEOAPIFY_API_KEY is configured in setup.env or environment.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

_REPO_ROOT = Path(__file__).parent.parent
_PLACEHOLDER_GEO = "your_geoapify_api_key"
_CONFTEST_SENTINELS = {"test_geo_key", ""}


def _load_setup_env() -> dict[str, str]:
    env_path = _REPO_ROOT / "setup.env"
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


_env = _load_setup_env()
_geo_key: str = _env.get("GEOAPIFY_API_KEY") or os.getenv("GEOAPIFY_API_KEY", "")
_real_geo = bool(_geo_key) and _geo_key not in {_PLACEHOLDER_GEO, *_CONFTEST_SENTINELS}

pytestmark = pytest.mark.skipif(
    not _real_geo,
    reason="GEOAPIFY_API_KEY not configured in setup.env",
)


@pytest.fixture
async def integration_client(monkeypatch):
    monkeypatch.setenv("GEOAPIFY_API_KEY", _geo_key)
    monkeypatch.setenv("OPENCHARGEMAP_API_KEY", os.getenv("OPENCHARGEMAP_API_KEY", "test_ocm_key"))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


async def test_geo_endpoints_live_calgary_to_vancouver(integration_client: AsyncClient):
    geocode_response = await integration_client.get(
        "/api/geo/geocode",
        params={"query": "Calgary, Alberta", "limit": 1, "lang": "en"},
    )
    assert geocode_response.status_code == 200
    geocode_body = geocode_response.json()
    assert geocode_body["query"] == "Calgary, Alberta"
    assert geocode_body["total"] >= 1
    first = geocode_body["results"][0]
    assert first["formatted"]
    assert first["coordinates"]["lat"] is not None
    assert first["coordinates"]["lon"] is not None

    route_response = await integration_client.post(
        "/api/geo/route",
        json={
            "waypoints": [
                {"lat": 51.044733, "lon": -114.071883},   # Calgary, AB
                {"lat": 49.282729, "lon": -123.120738},   # Vancouver, BC
            ],
            "mode": "drive",
            "details": True,
        },
    )
    assert route_response.status_code == 200
    route_body = route_response.json()
    assert route_body["mode"] == "drive"
    assert route_body["total_distance_m"] is not None
    assert route_body["total_distance_m"] > 100000
    assert route_body["total_duration_s"] is not None
    assert route_body["geometry"] is not None
