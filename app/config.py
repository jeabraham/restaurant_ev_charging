from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    openchargemap_api_key: str
    geoapify_api_key: str


def load_settings() -> Settings:
    return Settings(
        openchargemap_api_key=os.getenv("OPENCHARGEMAP_API_KEY", ""),
        geoapify_api_key=os.getenv("GEOAPIFY_API_KEY", ""),
    )
