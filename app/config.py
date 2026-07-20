from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    openchargemap_api_key: str
    geoapify_api_key: str
    yelp_api_key: str
    google_places_api_key: str
    enable_reviews: bool
    enable_opening_hours: bool
    restaurant_search_geoapify: bool
    restaurant_search_google: bool


_SENTINEL_KEYS = {"", "your_openchargemap_api_key"}


def _parse_bool(value: str, default: bool) -> bool:
    return value.strip().lower() not in ("false", "0", "no") if value else default


def load_settings() -> Settings:
    ocm_key = os.getenv("OPENCHARGEMAP_API_KEY", "")
    if ocm_key in _SENTINEL_KEYS:
        raise RuntimeError(
            "OPENCHARGEMAP_API_KEY is not configured. "
            "Set a real API key in setup.env and restart the server."
        )
    enable_reviews = _parse_bool(os.getenv("ENABLE_REVIEWS", "true"), default=True)
    enable_opening_hours = _parse_bool(os.getenv("ENABLE_OPENING_HOURS", "true"), default=True)
    restaurant_search_geoapify = _parse_bool(os.getenv("RESTAURANT_SEARCH_GEOAPIFY", "1"), default=True)
    restaurant_search_google = _parse_bool(os.getenv("RESTAURANT_SEARCH_GOOGLE", "0"), default=False)
    if not restaurant_search_geoapify and not restaurant_search_google:
        raise RuntimeError(
            "At least one of RESTAURANT_SEARCH_GEOAPIFY or RESTAURANT_SEARCH_GOOGLE must be enabled."
        )
    return Settings(
        openchargemap_api_key=ocm_key,
        geoapify_api_key=os.getenv("GEOAPIFY_API_KEY", ""),
        yelp_api_key=os.getenv("YELP_API_KEY", ""),
        google_places_api_key=os.getenv("GOOGLE_PLACES_API_KEY", ""),
        enable_reviews=enable_reviews,
        enable_opening_hours=enable_opening_hours,
        restaurant_search_geoapify=restaurant_search_geoapify,
        restaurant_search_google=restaurant_search_google,
    )
