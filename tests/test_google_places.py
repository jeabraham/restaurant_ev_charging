"""Mocked unit tests for GooglePlacesClient methods."""
from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from app.clients.google_places import GooglePlacesClient
from app.clients.http import RetryingHttpClient
from app.errors import UpstreamHttpError

_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"


async def _client() -> GooglePlacesClient:
    http = RetryingHttpClient(httpx.AsyncClient())
    return GooglePlacesClient(http, "test_key")


@respx.mock
async def test_place_details_returns_result_with_weekday_text():
    respx.get(_DETAILS_URL).mock(
        return_value=Response(
            200,
            json={
                "status": "OK",
                "result": {
                    "business_status": "OPERATIONAL",
                    "opening_hours": {"weekday_text": ["Monday: 9:00 AM – 5:00 PM"]},
                },
            },
        )
    )
    client = await _client()
    result = await client.place_details("ChIJabc")
    assert result is not None
    assert result["business_status"] == "OPERATIONAL"
    assert result["opening_hours"]["weekday_text"] == ["Monday: 9:00 AM – 5:00 PM"]


@respx.mock
async def test_place_details_zero_results_returns_none():
    respx.get(_DETAILS_URL).mock(return_value=Response(200, json={"status": "ZERO_RESULTS"}))
    client = await _client()
    assert await client.place_details("ChIJmissing") is None


@respx.mock
async def test_place_details_error_status_raises():
    respx.get(_DETAILS_URL).mock(return_value=Response(200, json={"status": "REQUEST_DENIED"}))
    client = await _client()
    with pytest.raises(UpstreamHttpError):
        await client.place_details("ChIJdenied")


@respx.mock
async def test_nearby_food_places_paginates_next_page_token(monkeypatch):
    """nearby_food_places follows next_page_token to return results from multiple pages."""
    import asyncio

    async def _noop_sleep(_): pass
    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    page1 = {"status": "OK", "results": [{"name": "Restaurant A", "business_status": "OPERATIONAL"}], "next_page_token": "token123"}
    page2 = {"status": "OK", "results": [{"name": "Blowers & Grafton", "business_status": "OPERATIONAL"}]}

    call_count = 0

    def _side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return Response(200, json=page1)
        return Response(200, json=page2)

    respx.get(_NEARBY_URL).mock(side_effect=_side_effect)

    client = await _client()
    results = await client.nearby_food_places(latitude=53.277, longitude=-110.057, radius_m=2000)

    names = [r["name"] for r in results]
    assert "Restaurant A" in names
    assert "Blowers & Grafton" in names
    assert call_count == 2


@respx.mock
async def test_nearby_food_places_respects_max_pages(monkeypatch):
    """nearby_food_places stops after max_pages even if next_page_token is present."""
    import asyncio

    async def _noop_sleep(_): pass
    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    page = {"status": "OK", "results": [{"name": "Restaurant X", "business_status": "OPERATIONAL"}], "next_page_token": "token"}

    respx.get(_NEARBY_URL).mock(return_value=Response(200, json=page))

    client = await _client()
    results = await client.nearby_food_places(latitude=53.277, longitude=-110.057, radius_m=2000, max_pages=2)

    # With max_pages=2 we should have fetched exactly 2 pages (2 calls).
    assert len(results) == 2
