from __future__ import annotations

import os

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from app.main import app


def _ocm_station(*, station_id: int, power_kw: int, connection_type_id: int, connection_title: str):
    return {
        "ID": station_id,
        "AddressInfo": {
            "Title": "Test Charger",
            "Latitude": 51.4672,
            "Longitude": -109.1571,
            "Town": "Kindersley",
            "StateOrProvince": "Saskatchewan",
            "Country": {"Title": "Canada"},
        },
        "OperatorInfo": {"Title": "Test Network"},
        "StatusType": {"Title": "Operational", "IsOperational": True},
        "Connections": [
            {
                "PowerKW": power_kw,
                "ConnectionType": {"ID": connection_type_id, "Title": connection_title},
            }
        ],
    }


def _geoapify_features():
    return {
        "features": [
            {
                "properties": {
                    "place_id": "abc123",
                    "name": "Example Restaurant",
                    "formatted": "123 Main Street, Kindersley, SK",
                    "lat": 51.4668,
                    "lon": -109.1549,
                    "categories": ["catering.restaurant"],
                    "website": "https://example.com",
                    "city": "Kindersley",
                    "state": "Saskatchewan",
                    "country": "Canada",
                }
            },
            {
                "properties": {
                    "place_id": "abc123",
                    "name": "Example Restaurant",
                    "formatted": "123 Main Street, Kindersley, SK",
                    "lat": 51.4668,
                    "lon": -109.1549,
                    "categories": ["catering.restaurant"],
                    "website": "https://example.com",
                }
            },
            {
                "properties": {
                    "name": "Fast Food Only",
                    "formatted": "X",
                    "lat": 51.4668,
                    "lon": -109.1549,
                    "categories": ["catering.fast_food"],
                }
            },
        ]
    }


@pytest.fixture
async def test_client_with_yelp(monkeypatch):
    monkeypatch.setenv("YELP_API_KEY", "test_yelp_key")
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.fixture
async def test_client_with_google(monkeypatch):
    monkeypatch.delenv("YELP_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test_google_key")
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@respx.mock
async def test_endpoint_success_and_sorting(test_client):
    respx.get("https://api.openchargemap.io/v3/poi").mock(
        return_value=Response(
            200,
            json=[
                _ocm_station(station_id=100, power_kw=150, connection_type_id=33, connection_title="CCS"),
                _ocm_station(station_id=101, power_kw=250, connection_type_id=30, connection_title="Tesla"),
            ],
        )
    )
    respx.get("https://api.geoapify.com/v2/places").mock(return_value=Response(200, json=_geoapify_features()))

    response = await test_client.post(
        "/find-dining-chargers",
        json={
            "latitude": 51.467,
            "longitude": -109.156,
            "radius_km": 10,
            "restaurant_radius_m": 500,
            "nacs": True,
            "ccs": True,
            "tesla_only": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["diagnostics"]["openchargemap_locations_received"] == 2
    assert body["diagnostics"]["qualifying_chargers"] == 2
    assert body["diagnostics"]["geoapify_places_received"] == 6
    assert body["diagnostics"]["qualifying_restaurant_charger_pairs"] == 2
    assert body["results"][0]["distance"]["walking_distance_verified"] is False
    assert body["results"][0]["charger"]["maximum_power_kw"] >= body["results"][1]["charger"]["maximum_power_kw"]


@respx.mock
async def test_endpoint_enriches_with_yelp_reviews(test_client_with_yelp):
    """When YELP_API_KEY is configured, each restaurant gets a reviews field."""
    respx.get("https://api.openchargemap.io/v3/poi").mock(
        return_value=Response(200, json=[_ocm_station(station_id=100, power_kw=150, connection_type_id=33, connection_title="CCS")])
    )
    respx.get("https://api.geoapify.com/v2/places").mock(return_value=Response(200, json=_geoapify_features()))
    respx.get("https://api.yelp.com/v3/businesses/search").mock(
        return_value=Response(
            200,
            json={
                "businesses": [
                    {
                        "name": "Example Restaurant",
                        "rating": 4.5,
                        "review_count": 320,
                        "price": "$$",
                        "is_closed": False,
                        "url": "https://yelp.com/biz/example-restaurant",
                        "categories": [{"alias": "canadian", "title": "Canadian"}],
                    }
                ]
            },
        )
    )

    response = await test_client_with_yelp.post(
        "/find-dining-chargers",
        json={"latitude": 51.467, "longitude": -109.156},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    # At least one result should have been enriched with Yelp reviews
    enriched = [r for r in results if r["restaurant"].get("reviews")]
    assert enriched, "Expected at least one restaurant to have a reviews field"
    reviews = enriched[0]["restaurant"]["reviews"]
    assert reviews["rating"] == 4.5
    assert reviews["review_count"] == 320
    assert reviews["price_level"] == "$$"
    assert reviews["is_open_now"] is True
    assert reviews["provider"] == "yelp"
    assert reviews["provider_url"] == "https://yelp.com/biz/example-restaurant"
    assert reviews["cuisine_types"] == ["Canadian"]


@respx.mock
async def test_endpoint_yelp_failure_does_not_break_response(test_client_with_yelp):
    """A Yelp API failure leaves restaurants without reviews but does not fail the request."""
    respx.get("https://api.openchargemap.io/v3/poi").mock(
        return_value=Response(200, json=[_ocm_station(station_id=100, power_kw=150, connection_type_id=33, connection_title="CCS")])
    )
    respx.get("https://api.geoapify.com/v2/places").mock(return_value=Response(200, json=_geoapify_features()))
    respx.get("https://api.yelp.com/v3/businesses/search").mock(return_value=Response(500, json={"error": "internal"}))

    response = await test_client_with_yelp.post(
        "/find-dining-chargers",
        json={"latitude": 51.467, "longitude": -109.156},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) > 0
    # No restaurant should have a reviews field because Yelp failed
    for result in results:
        assert result["restaurant"].get("reviews") is None


@respx.mock
async def test_request_validation_semantic_422(test_client):
    response = await test_client.post(
        "/find-dining-chargers",
        json={
            "latitude": 51.467,
            "longitude": -109.156,
            "nacs": False,
            "ccs": False,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SEMANTIC_VALIDATION_ERROR"


@respx.mock
async def test_request_validation_invalid_param_400(test_client):
    response = await test_client.post(
        "/find-dining-chargers",
        json={
            "latitude": 151.467,
            "longitude": -109.156,
        },
    )
    assert response.status_code == 400


@respx.mock
async def test_upstream_error_returns_502(test_client):
    respx.get("https://api.openchargemap.io/v3/poi").mock(return_value=Response(503, json={"error": "down"}))

    response = await test_client.post(
        "/find-dining-chargers",
        json={
            "latitude": 51.467,
            "longitude": -109.156,
        },
    )

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "OPENCHARGEMAP_UPSTREAM_ERROR"
    assert body["error"]["upstream_status"] == 503


@respx.mock
async def test_no_results_warning(test_client):
    respx.get("https://api.openchargemap.io/v3/poi").mock(return_value=Response(200, json=[]))

    response = await test_client.post(
        "/find-dining-chargers",
        json={
            "latitude": 51.467,
            "longitude": -109.156,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert "No qualifying charger-restaurant pairs were found." in body["diagnostics"]["warnings"]


@respx.mock
async def test_endpoint_enriches_with_google_reviews(test_client_with_google):
    """When GOOGLE_PLACES_API_KEY is configured and YELP_API_KEY is absent, restaurants get Google reviews."""
    respx.get("https://api.openchargemap.io/v3/poi").mock(
        return_value=Response(200, json=[_ocm_station(station_id=100, power_kw=150, connection_type_id=33, connection_title="CCS")])
    )
    respx.get("https://api.geoapify.com/v2/places").mock(return_value=Response(200, json=_geoapify_features()))
    respx.get("https://maps.googleapis.com/maps/api/place/findplacefromtext/json").mock(
        return_value=Response(
            200,
            json={
                "candidates": [
                    {
                        "place_id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
                        "name": "Example Restaurant",
                        "rating": 4.2,
                        "user_ratings_total": 180,
                        "price_level": 2,
                        "opening_hours": {"open_now": True},
                        "types": ["canadian_restaurant", "restaurant", "food", "establishment"],
                    }
                ],
                "status": "OK",
            },
        )
    )

    response = await test_client_with_google.post(
        "/find-dining-chargers",
        json={"latitude": 51.467, "longitude": -109.156},
    )

    assert response.status_code == 200
    results = response.json()["results"]
    enriched = [r for r in results if r["restaurant"].get("reviews")]
    assert enriched, "Expected at least one restaurant to have a reviews field"
    reviews = enriched[0]["restaurant"]["reviews"]
    assert reviews["rating"] == 4.2
    assert reviews["review_count"] == 180
    assert reviews["price_level"] == "$$"
    assert reviews["is_open_now"] is True
    assert reviews["provider"] == "google"
    assert reviews["provider_url"] == "https://www.google.com/maps/search/?api=1&query=Example%20Restaurant&query_place_id=ChIJN1t_tDeuEmsRUsoyG83frY4"
    assert "Canadian Restaurant" in reviews["cuisine_types"]
