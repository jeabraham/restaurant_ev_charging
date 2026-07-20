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
