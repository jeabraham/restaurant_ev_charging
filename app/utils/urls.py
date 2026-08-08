from __future__ import annotations

from urllib.parse import quote, urlencode


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


def google_maps_place_url_from_id(name: str, place_id: str) -> str:
    """Build a Google Maps place link anchored to a specific Google place_id.

    Uses the /maps/place/ URL scheme with a place_id query to reliably open the
    exact business page without showing a list of other search results.
    """
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}"


def plugshare_google_search_url(name: str, city: str | None, network: str | None) -> str:
    query_parts = ['site:plugshare.com/location', name]
    if city:
        query_parts.append(city)
    if network:
        query_parts.append(network)

    query = " ".join(query_parts)
    encoded_query = urlencode({"q": query})
    return f"https://www.google.com/search?{encoded_query}"
