from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    openchargemap_api_key: str
    geoapify_api_key: str
    yelp_api_key: str
    enable_reviews: bool


_SENTINEL_KEYS = {"", "your_openchargemap_api_key"}


def load_settings() -> Settings:
    ocm_key = os.getenv("OPENCHARGEMAP_API_KEY", "")
    if ocm_key in _SENTINEL_KEYS:
        raise RuntimeError(
            "OPENCHARGEMAP_API_KEY is not configured. "
            "Set a real API key in setup.env and restart the server."
        )
    enable_reviews = os.getenv("ENABLE_REVIEWS", "true").strip().lower() not in ("false", "0", "no")
    return Settings(
        openchargemap_api_key=ocm_key,
        geoapify_api_key=os.getenv("GEOAPIFY_API_KEY", ""),
        yelp_api_key=os.getenv("YELP_API_KEY", ""),
        enable_reviews=enable_reviews,
    )
