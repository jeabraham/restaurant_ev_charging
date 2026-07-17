from __future__ import annotations

from urllib.parse import urlencode


def openchargemap_details_url(poi_id: int) -> str:
    return f"https://openchargemap.io/poi/details/{poi_id}"


def google_maps_walking_url(
    charger_lat: float,
    charger_lon: float,
    restaurant_lat: float,
    restaurant_lon: float,
) -> str:
    query = urlencode(
        {
            "api": 1,
            "origin": f"{charger_lat},{charger_lon}",
            "destination": f"{restaurant_lat},{restaurant_lon}",
            "travelmode": "walking",
        }
    )
    return f"https://www.google.com/maps/dir/?{query}"


def google_maps_place_url(latitude: float, longitude: float) -> str:
    query = urlencode({"api": 1, "query": f"{latitude},{longitude}"})
    return f"https://www.google.com/maps/search/?{query}"


def plugshare_google_search_url(name: str, city: str | None, network: str | None) -> str:
    query_parts = ['site:plugshare.com/location', f'"{name}"']
    if city:
        query_parts.append(f'"{city}"')
    if network:
        query_parts.append(f'"{network}"')

    query = " ".join(query_parts)
    encoded_query = urlencode({"q": query})
    return f"https://www.google.com/search?{encoded_query}"
