from __future__ import annotations

import logging
from typing import Any

from app.clients.http import RetryingHttpClient

logger = logging.getLogger(__name__)

_BUSINESS_SEARCH_URL = "https://api.yelp.com/v3/businesses/search"
_MATCH_RADIUS_M = 200


class YelpClient:
    def __init__(self, http_client: RetryingHttpClient, api_key: str) -> None:
        self._http_client = http_client
        self._api_key = api_key

    async def find_business(
        self,
        name: str,
        latitude: float,
        longitude: float,
    ) -> dict[str, Any] | None:
        """Search Yelp for a business by name near given coordinates.

        Returns the closest matching business dict, or None if no match is found.
        Raises UpstreamHttpError / UpstreamTimeoutError on API failures.
        """
        params = {
            "term": name,
            "latitude": latitude,
            "longitude": longitude,
            "radius": _MATCH_RADIUS_M,
            "limit": 1,
        }
        response = await self._http_client.get_json(
            url=_BUSINESS_SEARCH_URL,
            params=params,
            headers={"Authorization": "Bearer " + self._api_key},
            service_name="YELP",
        )
        businesses = response.get("businesses") if isinstance(response, dict) else None
        if not businesses:
            return None
        return businesses[0]
