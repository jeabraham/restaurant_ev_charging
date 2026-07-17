from __future__ import annotations

import re
from typing import Any

CCS_TYPE_IDS = {32, 33}
NACS_TYPE_IDS = {30, 31}
L2_TYPE_IDS = {1, 25}  # J1772 (North America) and IEC 62196 Type 2 (Mennekes)
FAST_CHARGE_MIN_KW = 50
L2_MIN_KW = 3.0  # Standard 16 A / 240 V Level 2 chargers deliver 3.3–3.7 kW; Level 1 (120 V) is ~1.4 kW

TESLA_ONLY_PATTERN = re.compile(r"(?:tesla[-\s]?only|only tesla|tesla vehicles only)", re.IGNORECASE)
NON_TESLA_PATTERN = re.compile(
    r"(?:non[-\s]?tesla|all ev|ccs|open to all)",
    re.IGNORECASE,
)


def normalize_connector(
    connection: dict[str, Any],
    requested_ccs: bool,
    requested_nacs: bool,
    requested_l2: bool = False,
) -> dict[str, Any] | None:
    connection_type = connection.get("ConnectionType") or {}
    title = str(connection_type.get("Title") or "")
    type_id = connection_type.get("ID")
    power_kw = connection.get("PowerKW")

    if power_kw is None or power_kw < L2_MIN_KW:
        return None

    is_fast = power_kw >= FAST_CHARGE_MIN_KW

    if is_fast:
        if requested_ccs and (type_id in CCS_TYPE_IDS or "ccs" in title.lower()):
            return {"type": "CCS", "level": "DC_FAST", "power_kw": power_kw}

        if requested_nacs and (
            type_id in NACS_TYPE_IDS
            or "nacs" in title.lower()
            or "tesla" in title.lower()
        ):
            return {"type": "NACS", "level": "DC_FAST", "power_kw": power_kw}

    if requested_l2 and (type_id in L2_TYPE_IDS or "j1772" in title.lower() or "type 2" in title.lower()):
        return {"type": "J1772" if (type_id == 1 or "j1772" in title.lower()) else "Type2", "level": "L2", "power_kw": power_kw}

    # NACS connectors below fast-charge speed (e.g. Tesla Destination Chargers at 16–48 kW).
    # Only included when the user explicitly requests L2 — a user asking for NACS without L2
    # wants DC fast (Superchargers) only, not slow destination chargers.
    if requested_l2 and requested_nacs and (type_id in NACS_TYPE_IDS or "nacs" in title.lower() or "tesla" in title.lower()):
        return {"type": "NACS", "level": "L2", "power_kw": power_kw}

    return None


def station_is_explicitly_non_operational(station: dict[str, Any]) -> bool:
    status = station.get("StatusType") or {}
    is_operational = status.get("IsOperational")
    return is_operational is False


def station_status_value(station: dict[str, Any]) -> str:
    status = station.get("StatusType") or {}
    title = status.get("Title")
    if title:
        return str(title)

    is_operational = status.get("IsOperational")
    if is_operational is True:
        return "Operational"
    if is_operational is False:
        return "NonOperational"
    return "Unknown"


def detect_tesla_restriction(station: dict[str, Any]) -> tuple[bool, bool]:
    usage_title = str((station.get("UsageType") or {}).get("Title") or "")
    general_comments = str(station.get("GeneralComments") or "")
    access_comments = str((station.get("AddressInfo") or {}).get("AccessComments") or "")
    searchable_text = " ".join([usage_title, general_comments, access_comments])

    explicit_tesla_only = bool(TESLA_ONLY_PATTERN.search(searchable_text))
    explicit_non_tesla_access = bool(NON_TESLA_PATTERN.search(searchable_text))

    return explicit_tesla_only, explicit_non_tesla_access


def dedupe_geoapify_place_key(place: dict[str, Any]) -> str:
    properties = place.get("properties") or {}
    place_id = properties.get("place_id")
    if place_id:
        return f"id:{place_id}"

    name = (properties.get("name") or "").strip().lower()
    address = (properties.get("formatted") or properties.get("address_line1") or "").strip().lower()
    lat = properties.get("lat")
    lon = properties.get("lon")
    return f"fallback:{name}|{address}|{round(float(lat), 6)}|{round(float(lon), 6)}"


def is_valid_website(url: str | None) -> bool:
    if not url or not isinstance(url, str):
        return False
    lowered = url.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


GOOGLE_FOOD_TYPES: dict[str, str] = {
    "restaurant": "catering.restaurant",
    "cafe": "catering.cafe",
    "bar": "catering.bar",
    "bakery": "catering.bakery",
    "meal_takeaway": "catering.fast_food",
    "meal_delivery": "catering.fast_food",
    "fast_food_restaurant": "catering.fast_food",
}


def google_place_to_geoapify_shape(place: dict[str, Any]) -> dict[str, Any]:
    """Convert a Google Places Nearby Search result to the Geoapify feature shape.

    The returned dict has the same {"properties": {...}} structure expected by
    is_qualifying_place(), dedupe_geoapify_place_key(), and the restaurant processing loop.
    Google `types` are mapped to synthetic `catering.*` categories so the existing
    catering filter works unchanged.
    """
    loc = (place.get("geometry") or {}).get("location") or {}
    types = place.get("types") or []
    categories = [GOOGLE_FOOD_TYPES[t] for t in types if t in GOOGLE_FOOD_TYPES]
    return {
        "properties": {
            "place_id": place.get("place_id"),
            "name": place.get("name"),
            "formatted": place.get("vicinity"),
            "lat": loc.get("lat"),
            "lon": loc.get("lng"),  # Google uses "lng", Geoapify uses "lon"
            "categories": categories,
            "website": None,        # Not available from Nearby Search
            "city": None,
            "state": None,
            "country": None,
        }
    }


def is_qualifying_place(place: dict[str, Any]) -> bool:
    properties = place.get("properties") or {}
    name = properties.get("name")
    if not isinstance(name, str) or not name.strip():
        return False

    if properties.get("lat") is None or properties.get("lon") is None:
        return False

    categories = properties.get("categories") or []
    return any(str(category).startswith("catering") for category in categories)
