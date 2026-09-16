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
                            "formatted": "Calgary, AB, Canada",
                            "lat": 51.044733,
                            "lon": -114.071883,
                            "country": "Canada",
                            "state": "Alberta",
                            "city": "Calgary",
                            "postcode": "T2P",
                            "place_id": "calgary-123",
                            "result_type": "city",
                            "rank": {"confidence": 1, "popularity": 4.6},
                        }
                    }
                ]
            },
        )
    )

    response = await test_client.get("/api/geo/geocode", params={"query": "Calgary, Alberta", "limit": 2, "lang": "en"})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "Calgary, Alberta"
    assert body["total"] == 1
    assert body["results"][0]["formatted"] == "Calgary, AB, Canada"
    assert body["results"][0]["coordinates"] == {"lat": 51.044733, "lon": -114.071883}
    assert body["results"][0]["components"]["country"] == "Canada"
    assert body["results"][0]["provider"]["place_id"] == "calgary-123"
    assert route.called
    assert route.calls[0].request.url.params["text"] == "Calgary, Alberta"
    assert route.calls[0].request.url.params["limit"] == "2"
    assert route.calls[0].request.url.params["lang"] == "en"


async def test_geocode_validation_limit_capped(test_client):
    response = await test_client.get("/api/geo/geocode", params={"query": "Calgary, Alberta", "limit": 11})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert body["error"]["message"] == "Invalid request payload."
    assert isinstance(body["error"]["details"], list)


@respx.mock
async def test_geocode_upstream_failure_maps_to_502(test_client):
    respx.get("https://api.geoapify.com/v1/geocode/search").mock(return_value=Response(503, json={"error": "down"}))
    response = await test_client.get("/api/geo/geocode", params={"query": "Calgary, Alberta"})
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "GEOAPIFY_UPSTREAM_ERROR"
    assert body["error"]["upstream_status"] == 503


@respx.mock
async def test_geocode_timeout_maps_to_502(test_client):
    respx.get("https://api.geoapify.com/v1/geocode/search").mock(side_effect=ReadTimeout("timeout"))
    response = await test_client.get("/api/geo/geocode", params={"query": "Calgary, Alberta"})
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
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[-114.071883, 51.044733], [-123.120738, 49.282729]],
                        },
                        "properties": {
                            "distance": 970000.0,
                            "time": 36000.0,
                            "polyline": "calgary-to-vancouver",
                            "legs": [
                                {
                                    "distance": 2510.5,
                                    "time": 440.2,
                                    "steps": [
                                        {
                                            "instruction": "Head west",
                                            "distance": 15000.0,
                                            "time": 900.0,
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
                {"lat": 51.044733, "lon": -114.071883},
                {"lat": 49.282729, "lon": -123.120738},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "drive"
    assert body["total_distance_m"] == 970000.0
    assert body["total_duration_s"] == 36000.0
    assert body["polyline"] == "calgary-to-vancouver"
    assert body["legs"][0]["steps"][0]["instruction"] == "Head west"
    assert route.called
    assert route.calls[0].request.url.params["waypoints"] == "51.044733,-114.071883|49.282729,-123.120738"
    assert route.calls[0].request.url.params["details"] == "instruction_details"


@respx.mock
async def test_route_omits_details_when_disabled(test_client):
    route = respx.get("https://api.geoapify.com/v1/routing").mock(
        return_value=Response(
            200,
            json={
                "features": [
                    {
                        "geometry": {"type": "LineString", "coordinates": []},
                        "properties": {"distance": 900.0, "time": 650.0},
                    }
                ]
            },
        )
    )
    response = await test_client.post(
        "/api/geo/route",
        json={
            "waypoints": [{"lat": 51.044733, "lon": -114.071883}, {"lat": 51.05, "lon": -114.07}],
            "details": False,
        },
    )
    assert response.status_code == 200
    assert "details" not in route.calls[0].request.url.params


@respx.mock
async def test_route_non_default_mode_propagates(test_client):
    route = respx.get("https://api.geoapify.com/v1/routing").mock(
        return_value=Response(
            200,
            json={
                "features": [
                    {
                        "geometry": {"type": "LineString", "coordinates": [[-114.071883, 51.044733], [-114.07, 51.05]]},
                        "properties": {"distance": 900.0, "time": 650.0, "legs": []},
                    }
                ]
            },
        )
    )
    response = await test_client.post(
        "/api/geo/route",
        json={
            "waypoints": [{"lat": 51.044733, "lon": -114.071883}, {"lat": 51.05, "lon": -114.07}],
            "mode": "walk",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "walk"
    assert route.called
    assert route.calls[0].request.url.params["mode"] == "walk"


@respx.mock
async def test_route_normalizes_numeric_string_fields(test_client):
    respx.get("https://api.geoapify.com/v1/routing").mock(
        return_value=Response(
            200,
            json={
                "features": [
                    {
                        "geometry": {"type": "LineString", "coordinates": [[-114.071883, 51.044733], [-114.07, 51.05]]},
                        "properties": {
                            "distance": "900.5",
                            "time": "650.2",
                            "legs": [
                                {
                                    "distance": "120.1",
                                    "time": "85.8",
                                    "steps": [
                                        {
                                            "instruction": "Walk straight",
                                            "distance": "50.6",
                                            "time": "33.4",
                                            "from_index": "2",
                                            "to_index": "4",
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
            "waypoints": [{"lat": 51.044733, "lon": -114.071883}, {"lat": 51.05, "lon": -114.07}],
            "mode": "walk",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_distance_m"] == 900.5
    assert body["total_duration_s"] == 650.2
    assert body["legs"][0]["distance_m"] == 120.1
    assert body["legs"][0]["duration_s"] == 85.8
    assert body["legs"][0]["steps"][0]["distance_m"] == 50.6
    assert body["legs"][0]["steps"][0]["duration_s"] == 33.4
    assert body["legs"][0]["steps"][0]["from_index"] == 2
    assert body["legs"][0]["steps"][0]["to_index"] == 4


@respx.mock
async def test_route_fractional_step_indexes_become_none(test_client):
    respx.get("https://api.geoapify.com/v1/routing").mock(
        return_value=Response(
            200,
            json={
                "features": [
                    {
                        "geometry": {"type": "LineString", "coordinates": [[-114.071883, 51.044733], [-114.07, 51.05]]},
                        "properties": {
                            "distance": 100.0,
                            "time": 50.0,
                            "legs": [
                                {
                                    "distance": 100.0,
                                    "time": 50.0,
                                    "steps": [
                                        {"instruction": "Walk", "distance": 10.0, "time": 5.0, "from_index": "2.5", "to_index": 4.7}
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
            "waypoints": [{"lat": 51.044733, "lon": -114.071883}, {"lat": 51.05, "lon": -114.07}],
            "mode": "walk",
        },
    )
    assert response.status_code == 200
    step = response.json()["legs"][0]["steps"][0]
    assert step["from_index"] is None
    assert step["to_index"] is None


async def test_route_validation_invalid_payload(test_client):
    response = await test_client.post(
        "/api/geo/route",
        json={"waypoints": [{"lat": 43.651, "lon": -79.383}]},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert body["error"]["message"] == "Invalid request payload."
    assert isinstance(body["error"]["details"], list)


@respx.mock
async def test_route_upstream_failure_maps_to_502(test_client):
    respx.get("https://api.geoapify.com/v1/routing").mock(return_value=Response(500, json={"error": "down"}))
    response = await test_client.post(
        "/api/geo/route",
        json={
            "waypoints": [{"lat": 51.044733, "lon": -114.071883}, {"lat": 49.282729, "lon": -123.120738}],
            "mode": "walk",
        },
    )
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "GEOAPIFY_UPSTREAM_ERROR"
    assert body["error"]["upstream_status"] == 500


@respx.mock
async def test_route_timeout_maps_to_502(test_client):
    respx.get("https://api.geoapify.com/v1/routing").mock(side_effect=ReadTimeout("timeout"))
    response = await test_client.post(
        "/api/geo/route",
        json={"waypoints": [{"lat": 51.044733, "lon": -114.071883}, {"lat": 49.282729, "lon": -123.120738}]},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "GEOAPIFY_UPSTREAM_TIMEOUT"


@respx.mock
async def test_route_no_features_maps_to_502(test_client):
    respx.get("https://api.geoapify.com/v1/routing").mock(return_value=Response(200, json={"features": []}))
    response = await test_client.post(
        "/api/geo/route",
        json={"waypoints": [{"lat": 51.044733, "lon": -114.071883}, {"lat": 49.282729, "lon": -123.120738}]},
    )
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "GEOAPIFY_UPSTREAM_ERROR"
    assert body["error"]["message"] == "Geoapify returned no route data."


@respx.mock
async def test_route_malformed_feature_maps_to_502(test_client):
    respx.get("https://api.geoapify.com/v1/routing").mock(return_value=Response(200, json={"features": ["bad"]}))
    response = await test_client.post(
        "/api/geo/route",
        json={"waypoints": [{"lat": 51.044733, "lon": -114.071883}, {"lat": 49.282729, "lon": -123.120738}]},
    )
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "GEOAPIFY_UPSTREAM_ERROR"
    assert body["error"]["message"] == "Geoapify returned no route data."


@respx.mock
async def test_route_malformed_properties_maps_to_502(test_client):
    respx.get("https://api.geoapify.com/v1/routing").mock(
        return_value=Response(200, json={"features": [{"geometry": {"type": "LineString", "coordinates": []}}]})
    )
    response = await test_client.post(
        "/api/geo/route",
        json={"waypoints": [{"lat": 51.044733, "lon": -114.071883}, {"lat": 49.282729, "lon": -123.120738}]},
    )
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "GEOAPIFY_UPSTREAM_ERROR"
    assert body["error"]["message"] == "Geoapify returned no route data."


async def test_geo_endpoints_fail_when_geoapify_key_missing(test_client_without_geo_key):
    geocode_response = await test_client_without_geo_key.get(
        "/api/geo/geocode", params={"query": "Calgary, Alberta"}
    )
    route_response = await test_client_without_geo_key.post(
        "/api/geo/route",
        json={"waypoints": [{"lat": 51.044733, "lon": -114.071883}, {"lat": 49.282729, "lon": -123.120738}]},
    )
    assert geocode_response.status_code == 500
    geocode_body = geocode_response.json()
    assert geocode_body["error"]["code"] == "GEOAPIFY_NOT_CONFIGURED"
    assert geocode_body["error"]["message"] == "GEOAPIFY_API_KEY is not configured."
    assert route_response.status_code == 500
    route_body = route_response.json()
    assert route_body["error"]["code"] == "GEOAPIFY_NOT_CONFIGURED"
    assert route_body["error"]["message"] == "GEOAPIFY_API_KEY is not configured."
