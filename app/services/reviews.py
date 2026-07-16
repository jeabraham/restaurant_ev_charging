from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

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
            logger.warning("Yelp lookup failed for %r at (%.5f, %.5f)", name, latitude, longitude)
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

    return ReviewInfo(
        rating=float(business.get("rating") or 0.0),
        review_count=int(business.get("review_count") or 0),
        price_level=business.get("price"),
        cuisine_types=categories,
        is_open_now=is_open_now,
        provider_url=business.get("url", ""),
        provider="yelp",
    )
