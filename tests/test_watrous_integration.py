"""Real integration tests for the Watrous, SK charger and nearby restaurants.

These tests make live HTTP requests to OpenChargeMap, Geoapify, and Google Places.
They are skipped when real API keys are absent.

Background
----------
The charger at Watrous Mainline Motor Products (208 1st Ave E) is a CCS DC fast charger
(120 kW) that exists in OpenChargeMap but is not listed in Google Maps.  The root cause
of the search failure is that **Geoapify has no catering data for Watrous, SK** — it
returns zero restaurants.  Google Places does have data (8 restaurants, including the
Pepper Tree Family Restaurant).  Enabling Google restaurant search (RESTAURANT_SEARCH_GOOGLE)
is the fix for this location.

Run them explicitly:
    pytest tests/test_watrous_integration.py -v
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

# Watrous, SK — charger at Watrous Mainline Motor Products, 208 1st Ave E
_WATROUS_LAT = 51.6670
_WATROUS_LON = -105.4756
_CHARGER_SEARCH_RADIUS_KM = 2.0
_RESTAURANT_RADIUS_M = 1000   # generous: Pepper Tree is ~200 m away on Main St

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
    if not _real_ocm:
        pytest.skip("OPENCHARGEMAP_API_KEY not configured in setup.env")
    return OpenChargeMapClient(http_client, _ocm_key)


@pytest.fixture
async def geo_client(http_client):
    if not _real_geo:
        pytest.skip("GEOAPIFY_API_KEY not configured in setup.env")
    return GeoapifyClient(http_client, _geo_key)


@pytest.fixture
async def google_client(http_client):
    if not _real_google:
        pytest.skip("GOOGLE_PLACES_API_KEY not configured in setup.env")
    return GooglePlacesClient(http_client, _google_key)


# ---------------------------------------------------------------------------
# Test 1 — OpenChargeMap: charger at Watrous Mainline Motor Products is present
# ---------------------------------------------------------------------------


async def test_ocm_watrous_mainline_charger_is_present(ocm_client):
    """OpenChargeMap returns the Watrous Mainline Motor Products charger in raw results."""
    stations = await ocm_client.nearby_stations(
        latitude=_WATROUS_LAT,
        longitude=_WATROUS_LON,
        radius_km=_CHARGER_SEARCH_RADIUS_KM,
    )

    assert stations, (
        f"OpenChargeMap returned no stations within {_CHARGER_SEARCH_RADIUS_KM} km of Watrous, SK. "
        "Check that the search radius and coordinates are correct."
    )

    names = [
        (s.get("AddressInfo") or {}).get("Title") or ""
        for s in stations
    ]
    print(f"\nOCM stations near Watrous ({len(stations)} total):")
    for name in names:
        print(f"  - {name!r}")

    mainline = [
        s for s in stations
        if "mainline" in ((s.get("AddressInfo") or {}).get("Title") or "").lower()
    ]
    assert mainline, (
        f"No station with 'Mainline' in the name found near Watrous. "
        f"Stations found: {names}"
    )

    station = mainline[0]
    connections = station.get("Connections") or []
    conn_types = [
        (
            (c.get("ConnectionType") or {}).get("Title"),
            c.get("PowerKW"),
        )
        for c in connections
        if isinstance(c, dict)
    ]
    print(f"\nWatrous Mainline connector types: {conn_types}")

    # The charger is a CCS DC fast charger at 120 kW.  It should qualify with default
    # search settings (ccs=True, l2=False) — the OCM stage is not the bottleneck.
    power_kws = [c.get("PowerKW") for c in connections if isinstance(c, dict)]
    max_kw = max((p for p in power_kws if p is not None), default=0)
    assert max_kw >= 50, (
        f"Expected a DC fast charger (≥50 kW) at Watrous Mainline Motor Products, "
        f"but max power is {max_kw} kW."
    )


# ---------------------------------------------------------------------------
# Test 2 — Google Places: restaurants near the charger
# ---------------------------------------------------------------------------


async def test_google_places_restaurants_near_watrous_charger(google_client):
    """Google Places returns restaurants near the Watrous Mainline Motor Products charger."""
    places = await google_client.nearby_food_places(
        latitude=_WATROUS_LAT,
        longitude=_WATROUS_LON,
        radius_m=_RESTAURANT_RADIUS_M,
    )

    print(f"\nGoogle Places restaurants near Watrous charger ({len(places)} found):")
    for p in places:
        name = p.get("name", "<no name>")
        vicinity = p.get("vicinity", "")
        rating = p.get("rating", "n/a")
        status = p.get("business_status", "")
        print(f"  - {name!r}  ({vicinity})  rating={rating}  status={status}")

    assert places, (
        f"Google Places returned no restaurants within {_RESTAURANT_RADIUS_M} m "
        "of the Watrous Mainline Motor Products charger."
    )

    names_lower = [p.get("name", "").lower() for p in places]
    # The restaurant is listed as "PepperTree" (no space) in Google Places.
    pepper_tree_found = any("peppertree" in n or "pepper tree" in n for n in names_lower)
    print(f"\nPepper Tree found in Google results: {pepper_tree_found}")
    assert pepper_tree_found, (
        f"PepperTree Family Restaurant not found in Google results. Names: {names_lower}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Geoapify: restaurants near the charger
# ---------------------------------------------------------------------------


async def test_geoapify_restaurants_near_watrous_charger(geo_client):
    """Geoapify returns catering places near the Watrous Mainline Motor Products charger."""
    features = await geo_client.nearby_food_places(
        latitude=_WATROUS_LAT,
        longitude=_WATROUS_LON,
        radius_m=_RESTAURANT_RADIUS_M,
    )

    print(f"\nGeoapify restaurants near Watrous charger ({len(features)} found):")
    for f in features:
        props = f.get("properties") or {}
        name = props.get("name", "<no name>")
        address = props.get("formatted", "")
        categories = props.get("categories", [])
        print(f"  - {name!r}  {address}  categories={categories}")

    # Geoapify currently has no catering data for Watrous, SK — this is the root cause
    # of the search failure.  Google Places is the only provider that works here.
    # This assertion documents the known limitation; update it if Geoapify adds coverage.
    assert not features, (
        "Geoapify unexpectedly returned catering places for Watrous — "
        "the known coverage gap may have been fixed. Review and update this test."
    )
    print("\nGeoapify confirmed: no catering data for Watrous (known limitation).")
