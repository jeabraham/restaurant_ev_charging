from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.clients.google_places import GooglePlacesClient
from app.clients.yelp import YelpClient

logger = logging.getLogger(__name__)


@dataclass
class ReviewInfo:
    rating: float
    review_count: int
    price_level: str | None  # "$", "$$", "$$$", "$$$$"
    cuisine_types: list[str]
    is_open_now: bool | None  # None means unknown
    provider_url: str
    provider: str
    is_fast_food: bool = False


class ReviewProvider(Protocol):
    async def lookup(self, name: str, latitude: float, longitude: float) -> ReviewInfo | None:
        ...


class YelpReviewProvider:
    """Looks up restaurant quality data from Yelp Fusion."""

    def __init__(self, yelp_client: YelpClient) -> None:
        self._client = yelp_client

    async def lookup(self, name: str, latitude: float, longitude: float) -> ReviewInfo | None:
        """Return review info for the named restaurant near the given coordinates.

        Returns None if no matching business is found or if the Yelp request fails.
        Failures are logged as warnings and do not propagate to the caller.
        """
        try:
            business = await self._client.find_business(name, latitude, longitude)
        except Exception:
            logger.warning("Yelp lookup failed for %r", name)
            return None

        if business is None:
            return None

        return _parse_review_info(business)


def _parse_review_info(business: dict[str, Any]) -> ReviewInfo:
    categories = [
        cat["title"]
        for cat in (business.get("categories") or [])
        if isinstance(cat, dict) and cat.get("title")
    ]

    is_closed = business.get("is_closed")
    is_open_now: bool | None = (not is_closed) if is_closed is not None else None

    # Detect fast food from Yelp categories.
    is_fast_food = any(
        cat.get("alias") == "fastfood" or "fast food" in cat.get("title", "").lower()
        for cat in (business.get("categories") or [])
        if isinstance(cat, dict)
    )

    return ReviewInfo(
        rating=float(business.get("rating") or 0.0),
        review_count=int(business.get("review_count") or 0),
        price_level=business.get("price"),
        cuisine_types=categories,
        is_open_now=is_open_now,
        provider_url=business.get("url", ""),
        provider="yelp",
        is_fast_food=is_fast_food,
    )


# ---------------------------------------------------------------------------
# Google Places provider
# ---------------------------------------------------------------------------

_GOOGLE_PRICE_LEVEL: dict[int, str] = {1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}

# Generic type strings returned by the Google Places API that don't describe
# cuisine or food category.
_GOOGLE_GENERIC_TYPES = {
    "restaurant",
    "food",
    "point_of_interest",
    "establishment",
    "store",
    "health",
}


class GooglePlacesReviewProvider:
    """Looks up restaurant quality data from the Google Places API."""

    def __init__(self, google_client: GooglePlacesClient) -> None:
        self._client = google_client

    async def lookup(self, name: str, latitude: float, longitude: float) -> ReviewInfo | None:
        """Return review info for the named restaurant near the given coordinates.

        Returns None if no matching place is found or if the request fails.
        Failures are logged as warnings and do not propagate to the caller.
        """
        try:
            place = await self._client.find_place(name, latitude, longitude)
        except Exception:
            logger.warning("Google Places lookup failed for %r", name)
            return None

        if place is None:
            return None

        return _parse_google_place(place)


def _parse_google_place(place: dict[str, Any]) -> ReviewInfo:
    price_raw = place.get("price_level")
    price_level = _GOOGLE_PRICE_LEVEL.get(price_raw) if isinstance(price_raw, int) else None

    opening_hours = place.get("opening_hours") or {}
    open_now_raw = opening_hours.get("open_now")
    is_open_now: bool | None = open_now_raw if isinstance(open_now_raw, bool) else None

    types = place.get("types") or []
    is_fast_food = "fast_food_restaurant" in types

    cuisine_types = [
        t.replace("_", " ").title()
        for t in types
        if isinstance(t, str) and t not in _GOOGLE_GENERIC_TYPES
    ]

    name = place.get("name", "")
    place_id = place.get("place_id")
    provider_url = place.get("url")
    if not provider_url and place_id:
        # Construct a Google Maps search URL using the place_id.
        # We include the name as well for better compatibility/display.
        encoded_name = urllib.parse.quote(name)
        provider_url = f"https://www.google.com/maps/search/?api=1&query={encoded_name}&query_place_id={place_id}"

    return ReviewInfo(
        rating=float(place.get("rating") or 0.0),
        review_count=int(place.get("user_ratings_total") or 0),
        price_level=price_level,
        cuisine_types=cuisine_types,
        is_open_now=is_open_now,
        provider_url=provider_url or "",
        provider="google",
        is_fast_food=is_fast_food,
    )
