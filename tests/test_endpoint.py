from __future__ import annotations

import respx
from httpx import Response


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
