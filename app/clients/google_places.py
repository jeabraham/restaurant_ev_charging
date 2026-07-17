from __future__ import annotations

import logging
from typing import Any

from app.clients.http import RetryingHttpClient

logger = logging.getLogger(__name__)

_FIND_PLACE_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
_FIND_PLACE_FIELDS = "name,rating,user_ratings_total,price_level,opening_hours,url,types"
_MATCH_RADIUS_M = 200


class GooglePlacesClient:
    def __init__(self, http_client: RetryingHttpClient, api_key: str) -> None:
        self._http_client = http_client
        self._api_key = api_key

    async def find_place(
        self,
        name: str,
        latitude: float,
        longitude: float,
    ) -> dict[str, Any] | None:
        """Search Google Places for a business by name near given coordinates.

        Returns the first matching candidate dict, or None if no match is found.
        Raises UpstreamHttpError / UpstreamTimeoutError on API failures.
        """
        params = {
            "input": name,
            "inputtype": "textquery",
            "fields": _FIND_PLACE_FIELDS,
            "locationbias": f"circle:{_MATCH_RADIUS_M}@{latitude},{longitude}",
            "key": self._api_key,
        }
        response = await self._http_client.get_json(
            url=_FIND_PLACE_URL,
            params=params,
            headers=None,
            service_name="GOOGLE_PLACES",
        )
        candidates = response.get("candidates") if isinstance(response, dict) else None
        if not candidates:
            return None
        return candidates[0]
