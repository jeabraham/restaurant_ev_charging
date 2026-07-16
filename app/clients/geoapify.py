from __future__ import annotations

from typing import Any

from app.clients.http import RetryingHttpClient


class GeoapifyClient:
    base_url = "https://api.geoapify.com/v2/places"

    def __init__(self, http_client: RetryingHttpClient, api_key: str) -> None:
        self._http_client = http_client
        self._api_key = api_key

    async def nearby_food_places(
        self,
        latitude: float,
        longitude: float,
        radius_m: int,
    ) -> list[dict[str, Any]]:
        categories = ",".join(
            [
                "catering.restaurant",
                "catering.cafe",
                "catering.pub",
                "catering.bar",
                "catering",
            ]
        )
        params = {
            "filter": f"circle:{longitude},{latitude},{radius_m}",
            "bias": f"proximity:{longitude},{latitude}",
            "categories": categories,
            "limit": 200,
            "apiKey": self._api_key,
        }

        response = await self._http_client.get_json(
            url=self.base_url,
            params=params,
            headers={"Accept": "application/json"},
            service_name="GEOAPIFY",
        )
        features = response.get("features") if isinstance(response, dict) else None
        if isinstance(features, list):
            return [item for item in features if isinstance(item, dict)]
        return []
