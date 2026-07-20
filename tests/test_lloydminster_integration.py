"""Real integration tests for the Lloydminster charger and Blowers and Grafton restaurant.

These tests make live HTTP requests to OpenChargeMap, Geoapify, and Google Places.
They are skipped when real API keys are absent.

Background
----------
Blowers and Grafton (a pub/restaurant rated 4.8 on Google) is located in Lloydminster,
AB/SK near the EV charger at OpenChargeMap POI 234683.  Despite its high rating it did
not appear in search results.

Root cause
----------
Geoapify has no catering data for the immediate area around the Lloydminster charger.
With ``RESTAURANT_SEARCH_GOOGLE`` disabled (the default), the pipeline found zero
restaurants and returned nothing for this charger.

Fix
---
When Geoapify returns zero catering places for a charger AND a Google client is
configured, the service now automatically falls back to Google Places restaurant search
for that charger.  This mirrors the existing OCM → Google charger-discovery fallback and
requires no extra configuration from the operator.

Google Maps:        https://maps.app.goo.gl/PFAhX36UzXFoa15JA
OpenChargeMap POI:  https://openchargemap.io/poi/details/234683

Run them explicitly:
    pytest tests/test_lloydminster_integration.py -v
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

# ---------------------------------------------------------------------------
# Load API keys from setup.env or environment
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_PLACEHOLDER_OCM = "your_openchargemap_api_key"
_PLACEHOLDER_GEO = "your_geoapify_api_key"
_PLACEHOLDER_GOOGLE = "your_google_places_api_key"
_CONFTEST_SENTINELS = {"test_ocm_key", "test_geo_key", "test_google_key"}


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

# Lloydminster town-centre coordinates (on the AB/SK border).
# The charger at OCM POI 234683 is resolved from OpenChargeMap within the tests.
_LLOYDMINSTER_LAT = 53.2775
_LLOYDMINSTER_LON = -110.0081
_CHARGER_SEARCH_RADIUS_KM = 5.0
_RESTAURANT_RADIUS_M = 1000
_OCM_POI_ID = 234683
_RESTAURANT_NAME = "Blowers and Grafton"

pytestmark = pytest.mark.skipif(
    not (_real_ocm and _real_geo and _real_google),
    reason="OPENCHARGEMAP_API_KEY, GEOAPIFY_API_KEY, or GOOGLE_PLACES_API_KEY not configured in setup.env",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _lloydminster_charger_station(ocm_client: OpenChargeMapClient) -> dict[str, Any]:
    stations = await ocm_client.nearby_stations(
        latitude=_LLOYDMINSTER_LAT,
        longitude=_LLOYDMINSTER_LON,
        radius_km=_CHARGER_SEARCH_RADIUS_KM,
    )
    assert stations, (
        f"OpenChargeMap returned no stations within {_CHARGER_SEARCH_RADIUS_KM} km of Lloydminster."
    )

    for station in stations:
        if station.get("ID") == _OCM_POI_ID:
            return station

    names = [
        (station.get("AddressInfo") or {}).get("Title") or ""
        for station in stations
    ]
    pytest.fail(
        f"OpenChargeMap did not return POI {_OCM_POI_ID} near Lloydminster. "
        f"Stations found: {names}"
    )


def _station_coordinates(station: dict[str, Any]) -> tuple[float, float]:
    address = station.get("AddressInfo") or {}
    lat = address.get("Latitude")
    lon = address.get("Longitude")
    assert isinstance(lat, (int, float)) and isinstance(lon, (int, float)), (
        f"Station {_OCM_POI_ID} is missing coordinates: {address}"
    )
    return float(lat), float(lon)


def _contains_restaurant(names: list[str]) -> bool:
    return any("blowers" in name.lower() for name in names)


# ---------------------------------------------------------------------------
# Test 1 — OpenChargeMap: charger POI 234683 is present
# ---------------------------------------------------------------------------


async def test_ocm_lloydminster_charger_is_present(ocm_client):
    """OpenChargeMap returns POI 234683 near Lloydminster."""
    station = await _lloydminster_charger_station(ocm_client)

    address = station.get("AddressInfo") or {}
    title = address.get("Title") or ""
    print(f"\nLloydminster OCM charger: id={station.get('ID')} title={title!r} address={address}")

    assert station["ID"] == _OCM_POI_ID
    assert title, "Expected the Lloydminster charger to have a title in OpenChargeMap."


# ---------------------------------------------------------------------------
# Test 2 — Geoapify: document coverage near the charger
# ---------------------------------------------------------------------------


async def test_geoapify_restaurants_near_lloydminster_charger(geo_client, ocm_client):
    """Document what Geoapify returns near the Lloydminster charger.

    Geoapify may have a catering coverage gap for this area (as it does for Watrous, SK).
    This test prints what Geoapify finds so failures are easy to diagnose.
    If Geoapify does return Blowers and Grafton the automatic Google fallback is a no-op
    and the test still passes.
    """
    station = await _lloydminster_charger_station(ocm_client)
    charger_lat, charger_lon = _station_coordinates(station)

    features = await geo_client.nearby_food_places(
        latitude=charger_lat,
        longitude=charger_lon,
        radius_m=_RESTAURANT_RADIUS_M,
    )

    names = [
        (f.get("properties") or {}).get("name", "<no name>")
        for f in features
    ]
    print(f"\nGeoapify catering places near Lloydminster charger ({len(features)} found): {names}")

    # Log whether Blowers and Grafton is present so failures are easy to diagnose.
    found = _contains_restaurant(names)
    print(f"Blowers and Grafton in Geoapify results: {found}")

    # We don't assert True/False here — Geoapify coverage may improve over time.
    # The end-to-end tests below assert the restaurant appears in service output.


# ---------------------------------------------------------------------------
# Test 3 — Google Places: Blowers and Grafton is present
# ---------------------------------------------------------------------------


async def test_google_places_finds_blowers_and_grafton(google_client, ocm_client):
    """Google Places returns Blowers and Grafton near the Lloydminster charger."""
    station = await _lloydminster_charger_station(ocm_client)
    charger_lat, charger_lon = _station_coordinates(station)

    places = await google_client.nearby_food_places(
        latitude=charger_lat,
        longitude=charger_lon,
        radius_m=_RESTAURANT_RADIUS_M,
    )

    names = [p.get("name", "") for p in places]
    print(f"\nGoogle Places restaurants near Lloydminster charger ({len(places)} found): {names}")

    assert _contains_restaurant(names), (
        f"Google Places did not return {_RESTAURANT_NAME!r} within {_RESTAURANT_RADIUS_M} m "
        f"of the Lloydminster charger (OCM POI {_OCM_POI_ID}). Names: {names}"
    )

    restaurant = next(p for p in places if "blowers" in (p.get("name") or "").lower())
    rating = restaurant.get("rating")
    print(f"\n{_RESTAURANT_NAME!r}: rating={rating} types={restaurant.get('types')}")
    assert isinstance(rating, (int, float)) and rating >= 4.0, (
        f"Expected {_RESTAURANT_NAME!r} to have a strong Google rating. Result: {restaurant}"
    )


# ---------------------------------------------------------------------------
# Test 4 — End-to-end: Blowers and Grafton appears using Google Places fallback
# ---------------------------------------------------------------------------


async def test_end_to_end_lloydminster_finds_blowers_and_grafton(http_client):
    """Full DiningChargerService search for Lloydminster returns Blowers and Grafton.

    This test exercises the automatic Google Places fallback that fires when Geoapify
    returns zero catering places for a charger.  No extra configuration is required;
    the fallback is triggered by a Google client being present and Geoapify returning
    nothing.
    """
    ocm = OpenChargeMapClient(http_client, _ocm_key)
    geo = GeoapifyClient(http_client, _geo_key)
    google = GooglePlacesClient(http_client, _google_key)

    service = DiningChargerService(
        ocm,
        geo,
        review_provider=None,
        google_client=google,
        restaurant_search_geoapify=True,
        restaurant_search_google=False,   # rely solely on the automatic fallback
        enable_charger_reviews=False,
        enable_opening_hours=False,
    )

    payload = FindDiningChargersRequest(
        latitude=_LLOYDMINSTER_LAT,
        longitude=_LLOYDMINSTER_LON,
        radius_km=_CHARGER_SEARCH_RADIUS_KM,
        restaurant_radius_m=_RESTAURANT_RADIUS_M,
        ccs=True,
        nacs=True,
    )

    data = await service.find(payload)
    results = data["results"]
    names = [item["restaurant"]["name"] for item in results]
    print(f"\nLloydminster end-to-end diagnostics: {data['diagnostics']}")
    print(f"Lloydminster end-to-end restaurant names: {names}")

    assert data["diagnostics"]["qualifying_chargers"] > 0, (
        "No qualifying chargers found near Lloydminster — "
        f"check OCM POI {_OCM_POI_ID} connector types and power level."
    )
    assert _contains_restaurant(names), (
        f"End-to-end search did not return {_RESTAURANT_NAME!r}. "
        "This is the regression being fixed: Geoapify coverage gap in Lloydminster "
        "should be covered by the automatic Google Places fallback. Names: {names}"
    )


# ---------------------------------------------------------------------------
# Test 5 — End-to-end with both sources explicitly enabled
# ---------------------------------------------------------------------------


async def test_end_to_end_lloydminster_both_sources_finds_blowers_and_grafton(http_client):
    """Blowers and Grafton appears when both Geoapify and Google Places are enabled."""
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
        latitude=_LLOYDMINSTER_LAT,
        longitude=_LLOYDMINSTER_LON,
        radius_km=_CHARGER_SEARCH_RADIUS_KM,
        restaurant_radius_m=_RESTAURANT_RADIUS_M,
        ccs=True,
        nacs=True,
    )

    data = await service.find(payload)
    results = data["results"]
    names = [item["restaurant"]["name"] for item in results]
    print(f"\nLloydminster both-sources diagnostics: {data['diagnostics']}")
    print(f"Lloydminster both-sources restaurant names: {names}")

    assert data["diagnostics"]["qualifying_chargers"] > 0, (
        "No qualifying chargers found near Lloydminster."
    )
    assert _contains_restaurant(names), (
        f"End-to-end search (both sources) did not return {_RESTAURANT_NAME!r}. Names: {names}"
    )
