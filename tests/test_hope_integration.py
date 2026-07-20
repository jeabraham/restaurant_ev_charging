"""Real integration tests for the Hope, BC Electrify Canada charger and Blue Moose Coffee House.

These tests make live HTTP requests to OpenChargeMap, Geoapify, and Google Places.
They are skipped when real API keys are absent.

Background
----------
Blue Moose Coffee House should be a strong recommendation near the Hope, BC
Electrify Canada charger (OpenChargeMap POI 168742): it is nearby and highly
rated.  These tests diagnose why it can still be missing from search results.

Run them explicitly:
    pytest tests/test_hope_integration.py -v
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.clients.geoapify import GeoapifyClient
from app.clients.google_places import GooglePlacesClient
from app.clients.http import RetryingHttpClient
from app.clients.openchargemap import OpenChargeMapClient
from app.schemas import FindDiningChargersRequest
from app.services.search import DiningChargerService

_REPO_ROOT = Path(__file__).parent.parent
_PLACEHOLDER_OCM = "your_openchargemap_api_key"
_PLACEHOLDER_GEO = "your_geoapify_api_key"
_PLACEHOLDER_GOOGLE = "your_google_places_api_key"
_CONFTEST_SENTINELS = {"test_ocm_key", "test_geo_key", "test_google_key"}

# Approximate Hope, BC town-centre coordinates. The exact charger coordinates are
# resolved from OpenChargeMap within the tests.
_HOPE_LAT = 49.3813
_HOPE_LON = -121.4417
_CHARGER_SEARCH_RADIUS_KM = 5.0
_RESTAURANT_RADIUS_M = 1000
_OCM_POI_ID = 168742
_BLUE_MOOSE_NAME = "Blue Moose Coffee House"
_GOOGLE_NEARBY_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"


def _load_setup_env() -> dict[str, str]:
    env_path = _REPO_ROOT / "setup.env"
    if not env_path.exists():
        return {}
    result: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


_env = _load_setup_env()
_ocm_key: str = _env.get("OPENCHARGEMAP_API_KEY") or os.getenv("OPENCHARGEMAP_API_KEY", "")
_geo_key: str = _env.get("GEOAPIFY_API_KEY") or os.getenv("GEOAPIFY_API_KEY", "")
_google_key: str = _env.get("GOOGLE_PLACES_API_KEY") or os.getenv("GOOGLE_PLACES_API_KEY", "")

_real_ocm = bool(_ocm_key) and _ocm_key not in ("", _PLACEHOLDER_OCM) and _ocm_key not in _CONFTEST_SENTINELS
_real_geo = bool(_geo_key) and _geo_key not in ("", _PLACEHOLDER_GEO) and _geo_key not in _CONFTEST_SENTINELS
_real_google = bool(_google_key) and _google_key not in ("", _PLACEHOLDER_GOOGLE) and _google_key not in _CONFTEST_SENTINELS

pytestmark = pytest.mark.skipif(
    not (_real_ocm and _real_geo and _real_google),
    reason="OPENCHARGEMAP_API_KEY, GEOAPIFY_API_KEY, or GOOGLE_PLACES_API_KEY not configured in setup.env",
)


@pytest.fixture
async def http_client():
    timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        yield RetryingHttpClient(client, retries=1)


@pytest.fixture
async def ocm_client(http_client):
    return OpenChargeMapClient(http_client, _ocm_key)


@pytest.fixture
async def geo_client(http_client):
    return GeoapifyClient(http_client, _geo_key)


@pytest.fixture
async def google_client(http_client):
    return GooglePlacesClient(http_client, _google_key)


async def _hope_charger_station(ocm_client: OpenChargeMapClient) -> dict[str, Any]:
    stations = await ocm_client.nearby_stations(
        latitude=_HOPE_LAT,
        longitude=_HOPE_LON,
        radius_km=_CHARGER_SEARCH_RADIUS_KM,
    )
    assert stations, f"OpenChargeMap returned no stations within {_CHARGER_SEARCH_RADIUS_KM} km of Hope, BC."

    for station in stations:
        if station.get("ID") == _OCM_POI_ID:
            return station

    names = [
        (station.get("AddressInfo") or {}).get("Title") or ""
        for station in stations
    ]
    pytest.fail(
        f"OpenChargeMap did not return POI {_OCM_POI_ID} near Hope, BC. Stations found: {names}"
    )


def _station_coordinates(station: dict[str, Any]) -> tuple[float, float]:
    address = station.get("AddressInfo") or {}
    lat = address.get("Latitude")
    lon = address.get("Longitude")
    assert isinstance(lat, (int, float)) and isinstance(lon, (int, float)), (
        f"Station {_OCM_POI_ID} is missing coordinates: {address}"
    )
    return float(lat), float(lon)


def _blue_moose_names(items: list[dict[str, Any]]) -> list[str]:
    return [
        (item.get("name") or item.get("properties", {}).get("name") or "")
        for item in items
    ]


def _contains_blue_moose(names: list[str]) -> bool:
    return any("blue moose" in name.lower() for name in names)


async def test_ocm_hope_electrify_canada_charger_is_present(ocm_client):
    station = await _hope_charger_station(ocm_client)

    address = station.get("AddressInfo") or {}
    title = address.get("Title") or ""
    print(f"\nHope OCM charger: id={station.get('ID')} title={title!r} address={address}")

    assert station["ID"] == _OCM_POI_ID
    assert title, "Expected the Hope charger to have a title in OpenChargeMap."


async def test_google_keyword_cafe_search_finds_blue_moose(http_client, ocm_client):
    station = await _hope_charger_station(ocm_client)
    charger_lat, charger_lon = _station_coordinates(station)

    response = await http_client.get_json(
        url=_GOOGLE_NEARBY_SEARCH_URL,
        params={
            "location": f"{charger_lat},{charger_lon}",
            "radius": _RESTAURANT_RADIUS_M,
            "keyword": _BLUE_MOOSE_NAME,
            "type": "cafe",
            "key": _google_key,
        },
        headers=None,
        service_name="GOOGLE_PLACES",
    )

    assert isinstance(response, dict), f"Unexpected Google response payload: {response!r}"
    results = response.get("results") or []
    print(f"\nGoogle cafe keyword results near Hope charger: {results}")

    assert results, f"Google cafe keyword search returned no results for {_BLUE_MOOSE_NAME!r}."

    names = _blue_moose_names(results)
    assert _contains_blue_moose(names), (
        f"Google cafe keyword search did not return {_BLUE_MOOSE_NAME!r}. Names: {names}"
    )

    blue_moose = next(item for item in results if "blue moose" in (item.get("name") or "").lower())
    rating = blue_moose.get("rating")
    types = blue_moose.get("types") or []
    assert isinstance(rating, (int, float)) and rating >= 4.0, (
        f"Expected {_BLUE_MOOSE_NAME!r} to have a strong rating. Result: {blue_moose}"
    )
    assert "cafe" in types, f"Expected Google to classify {_BLUE_MOOSE_NAME!r} as a cafe. Types: {types}"


async def test_geoapify_nearby_food_search_near_hope_charger_misses_blue_moose(geo_client, ocm_client):
    station = await _hope_charger_station(ocm_client)
    charger_lat, charger_lon = _station_coordinates(station)

    features = await geo_client.nearby_food_places(
        latitude=charger_lat,
        longitude=charger_lon,
        radius_m=_RESTAURANT_RADIUS_M,
    )

    names = _blue_moose_names(features)
    print(f"\nGeoapify food places near Hope charger ({len(features)} found): {names}")

    assert not _contains_blue_moose(names), (
        f"Geoapify unexpectedly returned {_BLUE_MOOSE_NAME!r}. Names: {names}"
    )


async def test_google_restaurant_search_near_hope_charger_misses_blue_moose(google_client, ocm_client):
    station = await _hope_charger_station(ocm_client)
    charger_lat, charger_lon = _station_coordinates(station)

    places = await google_client.nearby_food_places(
        latitude=charger_lat,
        longitude=charger_lon,
        radius_m=_RESTAURANT_RADIUS_M,
    )

    names = _blue_moose_names(places)
    print(f"\nGoogle restaurant search near Hope charger ({len(places)} found): {names}")

    assert not _contains_blue_moose(names), (
        f"Google restaurant search unexpectedly returned {_BLUE_MOOSE_NAME!r}. Names: {names}"
    )


async def test_end_to_end_hope_search_still_omits_blue_moose(http_client):
    ocm = OpenChargeMapClient(http_client, _ocm_key)
    geo = GeoapifyClient(http_client, _geo_key)
    google = GooglePlacesClient(http_client, _google_key)

    service = DiningChargerService(
        ocm,
        geo,
        review_provider=None,
        google_client=google,
        restaurant_search_geoapify=True,
        restaurant_search_google=True,
        enable_charger_reviews=False,
        enable_opening_hours=False,
    )

    payload = FindDiningChargersRequest(
        latitude=_HOPE_LAT,
        longitude=_HOPE_LON,
        radius_km=_CHARGER_SEARCH_RADIUS_KM,
        restaurant_radius_m=_RESTAURANT_RADIUS_M,
        ccs=True,
        nacs=True,
    )

    data = await service.find(payload)
    results = data["results"]
    names = [item["restaurant"]["name"] for item in results]
    print(f"\nHope end-to-end diagnostics: {data['diagnostics']}")
    print(f"Hope end-to-end restaurant names: {names}")

    assert data["diagnostics"]["qualifying_chargers"] > 0, "No qualifying Hope chargers found."
    assert not _contains_blue_moose(names), (
        f"End-to-end search unexpectedly returned {_BLUE_MOOSE_NAME!r}. Names: {names}"
    )
