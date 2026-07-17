"""Integration test for the Google Places client using a real API key.

These tests make live HTTP requests to the Google Places API and are skipped
when GOOGLE_PLACES_API_KEY is absent or still set to the placeholder value in
setup.env.  They are intentionally NOT mocked — the whole point is to verify
that the real API works with the real key.

Run them explicitly:
    pytest tests/test_google_places_integration.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from app.clients.google_places import GooglePlacesClient
from app.clients.http import RetryingHttpClient

# ---------------------------------------------------------------------------
# Resolve the API key from setup.env (preferred) or the environment
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_PLACEHOLDER = "your_google_places_api_key"


def _load_setup_env() -> dict[str, str]:
    """Parse setup.env into a dict, ignoring blank lines and comments."""
    env_path = _REPO_ROOT / "setup.env"
    if not env_path.exists():
        return {}
    result: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


_env = _load_setup_env()
_api_key: str = _env.get("GOOGLE_PLACES_API_KEY") or os.getenv("GOOGLE_PLACES_API_KEY", "")

pytestmark = pytest.mark.skipif(
    not _api_key or _api_key == _PLACEHOLDER,
    reason="GOOGLE_PLACES_API_KEY not configured in setup.env (or is still the placeholder value)",
)

# A well-known restaurant with a permanent, high-traffic location that reliably
# appears in the Google Places index.
_KNOWN_RESTAURANT = "Shake Shack"
_KNOWN_LAT = 40.7412  # Madison Square Park, NYC — original Shake Shack location
_KNOWN_LON = -73.9882


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def google_client():
    async with httpx.AsyncClient() as http:
        retrying = RetryingHttpClient(http, retries=1)
        yield GooglePlacesClient(retrying, _api_key)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_google_places_finds_well_known_restaurant(google_client):
    """A real API call for a well-known restaurant returns a result with valid fields."""
    result = await google_client.find_place(_KNOWN_RESTAURANT, _KNOWN_LAT, _KNOWN_LON)

    assert result is not None, (
        f"Google Places returned no candidates for {_KNOWN_RESTAURANT!r} "
        f"near ({_KNOWN_LAT}, {_KNOWN_LON}). "
        "If the API key is correct, check that the Places API is enabled "
        "in your Google Cloud project and billing is active."
    )
    assert isinstance(result.get("name"), str), "Expected 'name' to be a string"
    assert isinstance(result.get("rating"), (int, float)), "Expected 'rating' to be a number"
    assert isinstance(result.get("user_ratings_total"), int), "Expected 'user_ratings_total' to be an int"
    assert result.get("business_status") in ("OPERATIONAL", "CLOSED_TEMPORARILY", "CLOSED_PERMANENTLY")
    assert result["rating"] >= 1.0, "Rating should be at least 1.0"
    assert result["user_ratings_total"] >= 1, "A well-known restaurant should have at least one review"


async def test_google_places_returns_types_list(google_client):
    """The 'types' field returned by the real API should be a non-empty list of strings."""
    result = await google_client.find_place(_KNOWN_RESTAURANT, _KNOWN_LAT, _KNOWN_LON)

    assert result is not None
    types = result.get("types")
    assert isinstance(types, list) and len(types) > 0, "Expected a non-empty 'types' list"
    assert all(isinstance(t, str) for t in types), "All elements of 'types' should be strings"


async def test_google_places_result_parses_to_review_info(google_client):
    """The raw API response can be parsed by _parse_google_place without error."""
    from app.services.reviews import _parse_google_place, GooglePlacesReviewProvider

    result = await google_client.find_place(_KNOWN_RESTAURANT, _KNOWN_LAT, _KNOWN_LON)
    assert result is not None

    info = _parse_google_place(result)
    assert info.provider == "google"
    assert info.rating >= 1.0
    assert info.review_count >= 1
    assert isinstance(info.cuisine_types, list)
    assert isinstance(info.provider_url, str)


async def test_google_places_no_match_returns_none(google_client):
    """An implausible restaurant name near a remote location returns None, not an error."""
    result = await google_client.find_place(
        "xXxImpossibleRestaurantNamexXx999",
        latitude=0.0,
        longitude=0.0,
    )
    assert result is None
