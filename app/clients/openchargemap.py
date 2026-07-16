from __future__ import annotations

from typing import Any

from app.clients.http import RetryingHttpClient


class OpenChargeMapClient:
    base_url = "https://api.openchargemap.io/v3/poi"

    def __init__(self, http_client: RetryingHttpClient, api_key: str) -> None:
        self._http_client = http_client
        self._api_key = api_key

    async def nearby_stations(self, latitude: float, longitude: float, radius_km: float) -> list[dict[str, Any]]:
        params = {
            "output": "json",
            "latitude": latitude,
            "longitude": longitude,
            "distance": radius_km,
            "distanceunit": "km",
            "maxresults": 200,
            "compact": False,
            "verbose": False,
            "key": self._api_key,
        }
        response = await self._http_client.get_json(
            url=self.base_url,
            params=params,
            headers={"Accept": "application/json"},
            service_name="OPENCHARGEMAP",
        )
        if isinstance(response, list):
            return [item for item in response if isinstance(item, dict)]
        return []
