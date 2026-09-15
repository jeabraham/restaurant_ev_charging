from __future__ import annotations

import pytest
import respx
from httpx import ASGITransport, AsyncClient, ReadTimeout, Response

from app.main import app


@pytest.fixture
async def test_client_without_geo_key(monkeypatch):
    monkeypatch.setenv("OPENCHARGEMAP_API_KEY", "test_ocm_key")
    monkeypatch.setenv("GEOAPIFY_API_KEY", "")
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@respx.mock
async def test_geocode_success_normalized(test_client):
    route = respx.get("https://api.geoapify.com/v1/geocode/search").mock(
        return_value=Response(
            200,
            json={
                "features": [
                    {
                        "properties": {
                            "formatted": "Berlin, Germany",
                            "lat": 52.517037,
                            "lon": 13.38886,
                            "country": "Germany",
                            "state": "Berlin",
                            "city": "Berlin",
                            "postcode": "10117",
                            "place_id": "abc-123",
                            "result_type": "city",
                            "rank": {"confidence": 1, "popularity": 4.6},
                        }
                    }
                ]
            },
        )
    )

    response = await test_client.get("/api/geo/geocode", params={"query": "Berlin", "limit": 2, "lang": "en"})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "Berlin"
    assert body["total"] == 1
    assert body["results"][0]["formatted"] == "Berlin, Germany"
    assert body["results"][0]["coordinates"] == {"lat": 52.517037, "lon": 13.38886}
    assert body["results"][0]["components"]["country"] == "Germany"
    assert body["results"][0]["provider"]["place_id"] == "abc-123"
    assert route.called
    assert route.calls[0].request.url.params["text"] == "Berlin"
    assert route.calls[0].request.url.params["limit"] == "2"
    assert route.calls[0].request.url.params["lang"] == "en"


async def test_geocode_validation_limit_capped(test_client):
    response = await test_client.get("/api/geo/geocode", params={"query": "Berlin", "limit": 11})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


@respx.mock
async def test_geocode_upstream_failure_maps_to_502(test_client):
    respx.get("https://api.geoapify.com/v1/geocode/search").mock(return_value=Response(503, json={"error": "down"}))
    response = await test_client.get("/api/geo/geocode", params={"query": "Berlin"})
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "GEOAPIFY_UPSTREAM_ERROR"
    assert body["error"]["upstream_status"] == 503


@respx.mock
async def test_geocode_timeout_maps_to_502(test_client):
    respx.get("https://api.geoapify.com/v1/geocode/search").mock(side_effect=ReadTimeout("timeout"))
    response = await test_client.get("/api/geo/geocode", params={"query": "Berlin"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "GEOAPIFY_UPSTREAM_TIMEOUT"


@respx.mock
async def test_route_success_normalized(test_client):
    route = respx.get("https://api.geoapify.com/v1/routing").mock(
        return_value=Response(
            200,
            json={
                "features": [
                    {
                        "geometry": {"type": "LineString", "coordinates": [[-79.38, 43.65], [-79.4, 43.67]]},
                        "properties": {
                            "distance": 2510.5,
                            "time": 440.2,
                            "polyline": "abc123",
                            "legs": [
                                {
                                    "distance": 2510.5,
                                    "time": 440.2,
                                    "steps": [
                                        {
                                            "instruction": "Head north",
                                            "distance": 120.0,
                                            "time": 35.5,
                                            "from_index": 0,
                                            "to_index": 1,
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                ]
            },
        )
    )
    response = await test_client.post(
        "/api/geo/route",
        json={
            "waypoints": [
                {"lat": 43.651, "lon": -79.383},
                {"lat": 43.671, "lon": -79.4},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "drive"
    assert body["total_distance_m"] == 2510.5
    assert body["total_duration_s"] == 440.2
    assert body["polyline"] == "abc123"
    assert body["legs"][0]["steps"][0]["instruction"] == "Head north"
    assert route.called
    assert route.calls[0].request.url.params["waypoints"] == "43.651,-79.383|43.671,-79.4"
    assert route.calls[0].request.url.params["details"] == "true"


async def test_route_validation_invalid_payload(test_client):
    response = await test_client.post(
        "/api/geo/route",
        json={"waypoints": [{"lat": 43.651, "lon": -79.383}]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


@respx.mock
async def test_route_upstream_failure_maps_to_502(test_client):
    respx.get("https://api.geoapify.com/v1/routing").mock(return_value=Response(500, json={"error": "down"}))
    response = await test_client.post(
        "/api/geo/route",
        json={"waypoints": [{"lat": 43.651, "lon": -79.383}, {"lat": 43.671, "lon": -79.4}], "mode": "walk"},
    )
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "GEOAPIFY_UPSTREAM_ERROR"
    assert body["error"]["upstream_status"] == 500


async def test_geo_endpoints_fail_when_geoapify_key_missing(test_client_without_geo_key):
    geocode_response = await test_client_without_geo_key.get("/api/geo/geocode", params={"query": "Berlin"})
    route_response = await test_client_without_geo_key.post(
        "/api/geo/route",
        json={"waypoints": [{"lat": 43.651, "lon": -79.383}, {"lat": 43.671, "lon": -79.4}]},
    )
    assert geocode_response.status_code == 500
    assert geocode_response.json()["error"]["code"] == "GEOAPIFY_NOT_CONFIGURED"
    assert route_response.status_code == 500
    assert route_response.json()["error"]["code"] == "GEOAPIFY_NOT_CONFIGURED"
