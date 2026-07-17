"""Real integration tests for charger-restaurant search near Canmore, AB.

These tests make live HTTP requests to OpenChargeMap, Geoapify, and optionally
Google Places.  They are skipped when real API keys are absent.

Run them explicitly:
    pytest tests/test_canmore_integration.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from app.clients.geoapify import GeoapifyClient
from app.clients.google_places import GooglePlacesClient
from app.clients.http import RetryingHttpClient
from app.clients.openchargemap import OpenChargeMapClient
from app.schemas import FindDiningChargersRequest
from app.services.search import DiningChargerService
from app.utils.distance import haversine_metres

# ---------------------------------------------------------------------------
# Load API keys from setup.env or environment
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_PLACEHOLDER_OCM = "your_openchargemap_api_key"
_PLACEHOLDER_GEO = "your_geoapify_api_key"
_PLACEHOLDER_GOOGLE = "your_google_places_api_key"


def _load_setup_env() -> dict[str, str]:
    env_path = _REPO_ROOT / "setup.env"
    if not env_path.exists():
        return {}
    result: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip optional leading "export " shell keyword.
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


_env = _load_setup_env()
# Prefer setup.env values; fall back to environment but exclude conftest.py sentinel values.
_CONFTEST_SENTINELS = {"test_ocm_key", "test_geo_key", "test_google_key"}
_ocm_key: str = _env.get("OPENCHARGEMAP_API_KEY") or os.getenv("OPENCHARGEMAP_API_KEY", "")
_geo_key: str = _env.get("GEOAPIFY_API_KEY") or os.getenv("GEOAPIFY_API_KEY", "")
_google_key: str = _env.get("GOOGLE_PLACES_API_KEY") or os.getenv("GOOGLE_PLACES_API_KEY", "")

_real_ocm = bool(_ocm_key) and _ocm_key not in ("", _PLACEHOLDER_OCM) and _ocm_key not in _CONFTEST_SENTINELS
_real_geo = bool(_geo_key) and _geo_key not in ("", _PLACEHOLDER_GEO) and _geo_key not in _CONFTEST_SENTINELS
_real_google = bool(_google_key) and _google_key not in ("", _PLACEHOLDER_GOOGLE) and _google_key not in _CONFTEST_SENTINELS

# Canmore, AB, Canada
_CANMORE_LAT = 51.0835
_CANMORE_LON = -115.3675
_SEARCH_RADIUS_KM = 5.0
_RESTAURANT_RADIUS_M = 500

pytestmark = pytest.mark.skipif(
    not (_real_ocm and _real_geo),
    reason="OPENCHARGEMAP_API_KEY or GEOAPIFY_API_KEY not configured in setup.env",
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
def canmore_request():
    return FindDiningChargersRequest(
        latitude=_CANMORE_LAT,
        longitude=_CANMORE_LON,
        radius_km=_SEARCH_RADIUS_KM,
        restaurant_radius_m=_RESTAURANT_RADIUS_M,
        ccs=True,
        nacs=True,
    )


@pytest.fixture
async def service_geoapify_only(http_client):
    """Service using only Geoapify for restaurant search — no Google key required."""
    ocm = OpenChargeMapClient(http_client, _ocm_key)
    geo = GeoapifyClient(http_client, _geo_key)
    return DiningChargerService(
        ocm,
        geo,
        restaurant_search_geoapify=True,
        restaurant_search_google=False,
    )


@pytest.fixture
async def service_google_only(http_client):
    """Service using only Google Places for restaurant search."""
    if not _real_google:
        pytest.skip("GOOGLE_PLACES_API_KEY not configured in setup.env")
    ocm = OpenChargeMapClient(http_client, _ocm_key)
    geo = GeoapifyClient(http_client, _geo_key)
    google = GooglePlacesClient(http_client, _google_key)
    return DiningChargerService(
        ocm,
        geo,
        google_client=google,
        restaurant_search_geoapify=False,
        restaurant_search_google=True,
    )


@pytest.fixture
async def service_both_sources(http_client):
    """Service using both Geoapify and Google Places for restaurant search."""
    if not _real_google:
        pytest.skip("GOOGLE_PLACES_API_KEY not configured in setup.env")
    ocm = OpenChargeMapClient(http_client, _ocm_key)
    geo = GeoapifyClient(http_client, _geo_key)
    google = GooglePlacesClient(http_client, _google_key)
    return DiningChargerService(
        ocm,
        geo,
        google_client=google,
        restaurant_search_geoapify=True,
        restaurant_search_google=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_result_structure(result: dict) -> None:
    restaurant = result["restaurant"]
    charger = result["charger"]
    distance = result["distance"]
    assert isinstance(restaurant["name"], str) and restaurant["name"].strip()
    assert isinstance(restaurant["latitude"], float)
    assert isinstance(restaurant["longitude"], float)
    assert isinstance(charger["maximum_power_kw"], (int, float))
    assert isinstance(distance["straight_line_metres"], (int, float))
    assert distance["straight_line_metres"] >= 0


# ---------------------------------------------------------------------------
# Tests — Geoapify-only (no Google key needed)
# ---------------------------------------------------------------------------


async def test_canmore_geoapify_only_finds_chargers_and_restaurants(
    service_geoapify_only, canmore_request
):
    """Geoapify-only search near Canmore returns at least one charger-restaurant pair."""
    data = await service_geoapify_only.find(canmore_request)

    diag = data["diagnostics"]
    assert diag["openchargemap_locations_received"] > 0, (
        "No OCM stations found near Canmore — is the search radius large enough?"
    )
    assert diag["qualifying_chargers"] > 0, "No qualifying chargers found near Canmore"

    results = data["results"]
    assert len(results) > 0, "Expected at least one charger-restaurant pair near Canmore"
    for r in results:
        _assert_result_structure(r)


async def test_canmore_geoapify_restaurants_are_geographically_plausible(
    service_geoapify_only, canmore_request
):
    """All returned restaurants are within the expected radius of Canmore."""
    data = await service_geoapify_only.find(canmore_request)
    results = data["results"]
    assert results, "No results to validate"

    max_expected_m = _SEARCH_RADIUS_KM * 1000 + _RESTAURANT_RADIUS_M
    for r in results:
        dist = haversine_metres(
            _CANMORE_LAT,
            _CANMORE_LON,
            r["restaurant"]["latitude"],
            r["restaurant"]["longitude"],
        )
        assert dist < max_expected_m, (
            f"Restaurant {r['restaurant']['name']!r} is {dist:.0f} m from Canmore — too far"
        )


# ---------------------------------------------------------------------------
# Tests — Google Places restaurant search (skipped without API key)
# ---------------------------------------------------------------------------


async def test_canmore_google_only_finds_chargers_and_restaurants(
    service_google_only, canmore_request
):
    """Google Places-only restaurant search near Canmore returns results."""
    data = await service_google_only.find(canmore_request)

    diag = data["diagnostics"]
    assert diag["qualifying_chargers"] > 0, "No qualifying chargers found near Canmore"

    results = data["results"]
    assert len(results) > 0, (
        "Expected at least one charger-restaurant pair using Google Places restaurants"
    )
    for r in results:
        _assert_result_structure(r)


async def test_canmore_both_sources_finds_results(service_both_sources, canmore_request):
    """Combined Geoapify + Google Places search near Canmore returns results."""
    data = await service_both_sources.find(canmore_request)

    results = data["results"]
    assert len(results) > 0, (
        "Expected at least one charger-restaurant pair using both search sources"
    )
    for r in results:
        _assert_result_structure(r)


async def test_canmore_both_sources_no_duplicate_restaurants(
    service_both_sources, canmore_request
):
    """When using both Geoapify and Google, no restaurant appears twice in results."""
    data = await service_both_sources.find(canmore_request)
    results = data["results"]
    assert results, "No results to validate"

    # Deduplicate check: same name at approximately the same location should not appear twice.
    seen: set[str] = set()
    for r in results:
        name = r["restaurant"]["name"].strip().lower()
        lat = round(r["restaurant"]["latitude"], 3)
        lon = round(r["restaurant"]["longitude"], 3)
        key = f"{name}|{lat}|{lon}"
        assert key not in seen, (
            f"Duplicate restaurant in results: {r['restaurant']['name']!r} at ({lat}, {lon})"
        )
        seen.add(key)


async def test_canmore_google_only_result_count_is_reasonable(
    service_google_only, canmore_request
):
    """Google-only search returns a sane number of results (not zero, not thousands)."""
    data = await service_google_only.find(canmore_request)
    count = len(data["results"])
    assert 1 <= count <= canmore_request.max_results, (
        f"Unexpected result count: {count}"
    )
