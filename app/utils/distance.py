from __future__ import annotations

import math

EARTH_RADIUS_METRES = 6_371_000


def haversine_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return int(round(EARTH_RADIUS_METRES * c))
