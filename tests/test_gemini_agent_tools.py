from __future__ import annotations

import importlib.util
from pathlib import Path

import respx
from httpx import Response

_MODULE_PATH = Path(__file__).parent.parent / "gemini_agent.py"
_SPEC = importlib.util.spec_from_file_location("gemini_agent", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
gemini_agent = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gemini_agent)


@respx.mock
def test_geocode_address_uses_local_geo_endpoint(monkeypatch):
    monkeypatch.setattr(gemini_agent, "API_URL", "http://testserver")
    respx.get("http://testserver/api/geo/geocode").mock(
        return_value=Response(
            200,
            json={
                "query": "Calgary, Alberta",
                "total": 1,
                "results": [
                    {
                        "formatted": "Calgary, AB, Canada",
                        "coordinates": {"lat": 51.044733, "lon": -114.071883},
                    }
                ],
            },
        )
    )

    result = gemini_agent._geocode("Calgary, Alberta")
    assert result["latitude"] == 51.044733
    assert result["longitude"] == -114.071883
    assert result["formatted_address"] == "Calgary, AB, Canada"


@respx.mock
def test_route_waypoints_uses_local_route_endpoint(monkeypatch):
    monkeypatch.setattr(gemini_agent, "API_URL", "http://testserver")
    route = respx.post("http://testserver/api/geo/route").mock(
        return_value=Response(
            200,
            json={
                "mode": "drive",
                "total_distance_m": 970000.0,
                "total_duration_s": 36000.0,
                "geometry": {"type": "LineString", "coordinates": [[-114.071883, 51.044733], [-123.120738, 49.282729]]},
                "polyline": None,
                "legs": [],
            },
        )
    )

    result = gemini_agent._route_waypoints(
        {
            "waypoints": [
                {"lat": 51.044733, "lon": -114.071883},
                {"lat": 49.282729, "lon": -123.120738},
            ]
        }
    )
    assert result["mode"] == "drive"
    assert route.called
