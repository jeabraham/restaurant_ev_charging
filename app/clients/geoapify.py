from __future__ import annotations

from typing import Any

from app.clients.http import RetryingHttpClient


class GeoapifyClient:
    places_base_url = "https://api.geoapify.com/v2/places"
    geocode_base_url = "https://api.geoapify.com/v1/geocode/search"
    routing_base_url = "https://api.geoapify.com/v1/routing"

    def __init__(self, http_client: RetryingHttpClient, api_key: str) -> None:
        self._http_client = http_client
        self._api_key = api_key

    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def nearby_food_places(
        self,
        latitude: float,
        longitude: float,
        radius_m: int,
    ) -> list[dict[str, Any]]:
        params = {
            "categories": "catering",
            "filter": f"circle:{longitude},{latitude},{radius_m}",
            "bias": f"proximity:{longitude},{latitude}",
            "limit": 200,
            "apiKey": self._api_key,
        }

        response = await self._http_client.get_json(
            url=self.places_base_url,
            params=params,
            headers={"Accept": "application/json"},
            service_name="GEOAPIFY",
        )
        features = response.get("features") if isinstance(response, dict) else None
        if isinstance(features, list):
            return [item for item in features if isinstance(item, dict)]
        return []

    async def geocode(
        self,
        *,
        query: str,
        limit: int,
        lang: str | None = None,
        filter_value: str | None = None,
        bias: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "text": query,
            "limit": limit,
            "apiKey": self._api_key,
        }
        if lang:
            params["lang"] = lang
        if filter_value:
            params["filter"] = filter_value
        if bias:
            params["bias"] = bias
        response = await self._http_client.get_json(
            url=self.geocode_base_url,
            params=params,
            headers={"Accept": "application/json"},
            service_name="GEOAPIFY",
            timeout_seconds=8.0,
            retries=0,
            timeout_error_status_code=502,
        )
        return response if isinstance(response, dict) else {}

    async def route(
        self,
        *,
        waypoints: str,
        mode: str,
        details: bool,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "waypoints": waypoints,
            "mode": mode,
            "details": "true" if details else "false",
            "apiKey": self._api_key,
        }
        response = await self._http_client.get_json(
            url=self.routing_base_url,
            params=params,
            headers={"Accept": "application/json"},
            service_name="GEOAPIFY",
            timeout_seconds=8.0,
            retries=0,
            timeout_error_status_code=502,
        )
        return response if isinstance(response, dict) else {}
