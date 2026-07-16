from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    openchargemap_api_key: str
    geoapify_api_key: str


_SENTINEL_KEYS = {"", "your_openchargemap_api_key"}


def load_settings() -> Settings:
    ocm_key = os.getenv("OPENCHARGEMAP_API_KEY", "")
    if ocm_key in _SENTINEL_KEYS:
        raise RuntimeError(
            "OPENCHARGEMAP_API_KEY is not configured. "
            "Set a real API key in setup.env and restart the server."
        )
    return Settings(
        openchargemap_api_key=ocm_key,
        geoapify_api_key=os.getenv("GEOAPIFY_API_KEY", ""),
    )
