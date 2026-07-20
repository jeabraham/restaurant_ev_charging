from __future__ import annotations

import logging
from typing import Any

from app.clients.http import RetryingHttpClient
from app.errors import UpstreamHttpError

logger = logging.getLogger(__name__)

_FIND_PLACE_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
_FIND_PLACE_FIELDS = "place_id,name,rating,user_ratings_total,price_level,opening_hours,types,business_status"
_PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
# weekday_text and periods are only returned by Place Details, not Find Place / Nearby Search.
_PLACE_DETAILS_FIELDS = "business_status,opening_hours"
_MATCH_RADIUS_M = 200
_NEARBY_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
_NEARBY_SEARCH_FIELDS = "place_id,name,geometry,rating,user_ratings_total,opening_hours,business_status,vicinity"
_NEARBY_FOOD_FIELDS = "place_id,name,geometry,rating,user_ratings_total,opening_hours,business_status,vicinity,types"


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
        if not isinstance(response, dict):
            return None

        status = response.get("status", "")
        if status not in ("OK", "ZERO_RESULTS", ""):
            raise UpstreamHttpError(
                code="GOOGLE_PLACES_UPSTREAM_ERROR",
                message=f"Google Places API returned status {status!r}.",
                status_code=502,
            )

        candidates = response.get("candidates")
        if not candidates:
            return None
        return candidates[0]

    async def place_details(self, place_id: str) -> dict[str, Any] | None:
        """Fetch Place Details for a place_id (business_status + full opening hours).

        Returns the ``result`` dict (which may contain ``opening_hours.weekday_text``),
        or None if no result is found. Raises UpstreamHttpError on API failures.
        """
        params = {
            "place_id": place_id,
            "fields": _PLACE_DETAILS_FIELDS,
            "key": self._api_key,
        }
        response = await self._http_client.get_json(
            url=_PLACE_DETAILS_URL,
            params=params,
            headers=None,
            service_name="GOOGLE_PLACES",
        )
        if not isinstance(response, dict):
            return None

        status = response.get("status", "")
        if status not in ("OK", "ZERO_RESULTS", ""):
            raise UpstreamHttpError(
                code="GOOGLE_PLACES_UPSTREAM_ERROR",
                message=f"Google Places Details returned status {status!r}.",
                status_code=502,
            )

        result = response.get("result")
        return result if isinstance(result, dict) else None

    async def search_ev_chargers(
        self,
        latitude: float,
        longitude: float,
        radius_m: int = 10000,
    ) -> list[dict[str, Any]]:
        """Search Google Places for EV charging stations near given coordinates.

        Returns a list of place dicts. Note: Google Places does not include connector
        type or power level data — these results are suitable as a fallback when
        OpenChargeMap returns nothing, but connector filtering cannot be applied.
        """
        params = {
            "location": f"{latitude},{longitude}",
            "radius": radius_m,
            "type": "electric_vehicle_charging_station",
            "fields": _NEARBY_SEARCH_FIELDS,
            "key": self._api_key,
        }
        response = await self._http_client.get_json(
            url=_NEARBY_SEARCH_URL,
            params=params,
            headers=None,
            service_name="GOOGLE_PLACES",
        )
        if not isinstance(response, dict):
            return []

        status = response.get("status", "")
        if status not in ("OK", "ZERO_RESULTS", ""):
            raise UpstreamHttpError(
                code="GOOGLE_PLACES_UPSTREAM_ERROR",
                message=f"Google Places Nearby Search returned status {status!r}.",
                status_code=502,
            )

        return response.get("results") or []

    async def nearby_food_places(
        self,
        latitude: float,
        longitude: float,
        radius_m: int = 500,
    ) -> list[dict[str, Any]]:
        """Search Google Places for restaurants and cafes near given coordinates.

        Returns raw Google place dicts. Permanently/temporarily closed places are
        filtered out here. Results must be normalised with google_place_to_geoapify_shape()
        before passing to the restaurant processing pipeline.
        """
        params = {
            "location": f"{latitude},{longitude}",
            "radius": radius_m,
            "type": "restaurant",
            "fields": _NEARBY_FOOD_FIELDS,
            "key": self._api_key,
        }
        response = await self._http_client.get_json(
            url=_NEARBY_SEARCH_URL,
            params=params,
            headers=None,
            service_name="GOOGLE_PLACES",
        )
        if not isinstance(response, dict):
            return []

        status = response.get("status", "")
        if status not in ("OK", "ZERO_RESULTS", ""):
            raise UpstreamHttpError(
                code="GOOGLE_PLACES_UPSTREAM_ERROR",
                message=f"Google Places Nearby Search returned status {status!r}.",
                status_code=502,
            )

        results = response.get("results") or []
        return [
            r for r in results
            if r.get("business_status") not in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY")
        ]
