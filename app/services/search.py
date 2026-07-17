from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from app.clients.geoapify import GeoapifyClient
from app.clients.google_places import GooglePlacesClient
from app.clients.openchargemap import OpenChargeMapClient
from app.schemas import FindDiningChargersRequest
from app.services.filtering import (
    dedupe_geoapify_place_key,
    detect_tesla_restriction,
    is_qualifying_place,
    is_valid_website,
    normalize_connector,
    station_is_explicitly_non_operational,
    station_status_value,
)
from app.services.reviews import ReviewProvider, is_google_place_closed
from app.utils.distance import haversine_metres
from app.utils.urls import (
    google_maps_place_url,
    google_maps_walking_url,
    openchargemap_details_url,
)


logger = logging.getLogger(__name__)

# Combined ranking weights.  Adjust these if the balance feels wrong.
# A 5-star restaurant is worth an extra ~300 m walk or ~60 kW of charger power.
_RATING_WEIGHT = 15.0
_OPEN_BONUS = 10.0      # confirmed open now
_CLOSED_PENALTY = 20.0  # confirmed closed now
_DEFAULT_RATING = 3.0   # used when no review data is available
_POWER_WEIGHT = 0.3     # kW → score points
_DISTANCE_COST = 0.05   # metres → score points lost
_FAST_FOOD_PENALTY = 50.0  # Penalty for fast food restaurants and chains
_PRICE_BONUS = 5.0      # Bonus per price level above "$"
_CHARGER_RATING_WEIGHT = 10.0  # Google charger rating → score points


class DiningChargerService:
    def __init__(
        self,
        ocm_client: OpenChargeMapClient,
        geo_client: GeoapifyClient,
        review_provider: ReviewProvider | None = None,
        google_client: GooglePlacesClient | None = None,
    ) -> None:
        self._ocm_client = ocm_client
        self._geo_client = geo_client
        self._review_provider = review_provider
        self._google_client = google_client

    async def find(self, payload: FindDiningChargersRequest) -> dict[str, Any]:
        locations = await self._ocm_client.nearby_stations(
            latitude=payload.latitude,
            longitude=payload.longitude,
            radius_km=payload.radius_km,
        )

        qualifying_chargers = []
        geoapify_places_received = 0
        locality_candidates: list[str] = []

        for station in locations:
            station_id = station.get("ID")
            address = station.get("AddressInfo") or {}
            station_lat = address.get("Latitude")
            station_lon = address.get("Longitude")
            if not isinstance(station_id, int):
                continue
            if station_lat is None or station_lon is None:
                continue

            connections = station.get("Connections") or []
            connector_types = []
            for connection in connections:
                if not isinstance(connection, dict):
                    continue
                normalized = normalize_connector(connection, payload.ccs, payload.nacs, payload.l2)
                if normalized:
                    connector_types.append(normalized)

            if not connector_types:
                continue

            if station_is_explicitly_non_operational(station):
                continue

            explicit_tesla_only, explicit_non_tesla_access = detect_tesla_restriction(station)
            has_nacs = any(c["type"] == "NACS" for c in connector_types)
            has_ccs = any(c["type"] == "CCS" for c in connector_types)

            compatibility_notes = None
            if explicit_tesla_only and not payload.tesla_only:
                continue
            if explicit_tesla_only and payload.tesla_only:
                compatibility_notes = "Station is explicitly marked Tesla-only in OpenChargeMap data."
            elif has_nacs and not has_ccs and not explicit_non_tesla_access:
                compatibility_notes = (
                    "OpenChargeMap data does not clearly indicate non-Tesla access for this Tesla/NACS connector."
                )

            if not has_ccs and not payload.tesla_only and not payload.nacs and has_nacs:
                continue

            locality = self._locality_from_station(station)
            if locality:
                locality_candidates.append(locality)

            qualifying_chargers.append(
                {
                    "name": (address.get("Title") or "Unknown Charger"),
                    "network": ((station.get("OperatorInfo") or {}).get("Title")),
                    "connector_types": sorted(
                        connector_types,
                        key=lambda connector: (connector["type"], -connector["power_kw"]),
                    ),
                    "maximum_power_kw": max(c["power_kw"] for c in connector_types),
                    "latitude": station_lat,
                    "longitude": station_lon,
                    "status": station_status_value(station),
                    "compatibility_notes": compatibility_notes,
                    "openchargemap_poi_id": station_id,
                    "openchargemap_url": openchargemap_details_url(station_id),
                }
            )

        # Try to fetch Google reviews for chargers if enabled.
        if self._google_client:
            async def enrich_charger(c: dict) -> dict | None:
                try:
                    place = await self._google_client.find_place(
                        c["name"], c["latitude"], c["longitude"]
                    )
                    if place:
                        # Check if the charger is closed in Google Places.
                        if is_google_place_closed(place):
                            logger.info(
                                "Excluding charger %r because it is marked as closed in Google Places.",
                                c["name"],
                            )
                            return None

                        # We use a similar structure to restaurant reviews but simplified.
                        c["reviews"] = {
                            "rating": float(place.get("rating") or 0.0),
                            "review_count": int(place.get("user_ratings_total") or 0),
                            "provider": "google",
                        }
                        if "url" in place:
                            c["reviews"]["provider_url"] = place["url"]
                except Exception:
                    logger.warning("Failed to fetch Google reviews for charger %r", c["name"])
                return c

            enriched_chargers = await asyncio.gather(
                *[enrich_charger(c) for c in qualifying_chargers]
            )
            qualifying_chargers = [c for c in enriched_chargers if c is not None]

        # Collect all (charger, distance) pairs per unique restaurant.
        restaurant_chargers: dict[str, dict] = {}

        async def fetch_places(
            charger_item: dict[str, Any]
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            places_found = await self._geo_client.nearby_food_places(
                latitude=charger_item["latitude"],
                longitude=charger_item["longitude"],
                radius_m=payload.restaurant_radius_m,
            )
            return charger_item, places_found

        charger_places_pairs = await asyncio.gather(
            *[fetch_places(c) for c in qualifying_chargers]
        )

        for charger, places in charger_places_pairs:
            geoapify_places_received += len(places)

            for place in places:
                props = place.get("properties") or {}
                place_name = props.get("name", "<no name>")
                place_cats = props.get("categories", [])

                if not is_qualifying_place(place):
                    logger.debug(
                        "place rejected by is_qualifying_place: name=%r categories=%s",
                        place_name,
                        place_cats,
                    )
                    continue

                properties = place["properties"]
                restaurant_lat = properties["lat"]
                restaurant_lon = properties["lon"]
                straight_line_metres = haversine_metres(
                    charger["latitude"],
                    charger["longitude"],
                    restaurant_lat,
                    restaurant_lon,
                )
                if straight_line_metres > payload.restaurant_radius_m:
                    logger.debug(
                        "place rejected by distance: name=%r distance_m=%.1f limit_m=%d",
                        place_name,
                        straight_line_metres,
                        payload.restaurant_radius_m,
                    )
                    continue

                locality = self._locality_from_geoapify(properties)
                if locality:
                    locality_candidates.append(locality)

                dedupe_key = dedupe_geoapify_place_key(place)
                pair = {
                    "charger": charger,
                    "distance": {
                        "straight_line_metres": straight_line_metres,
                        "estimated_walking_distance_metres": straight_line_metres,
                        "estimated_walking_time_minutes": int(
                            math.ceil(straight_line_metres / 60)
                        ),
                        "walking_distance_verified": False,
                        "google_maps_walking_url": google_maps_walking_url(
                            charger_lat=charger["latitude"],
                            charger_lon=charger["longitude"],
                            restaurant_lat=restaurant_lat,
                            restaurant_lon=restaurant_lon,
                        ),
                    },
                }

                if dedupe_key not in restaurant_chargers:
                    website = properties.get("website")
                    restaurant_chargers[dedupe_key] = {
                        "restaurant": {
                            "name": properties["name"].strip(),
                            "address": properties.get("formatted"),
                            "latitude": restaurant_lat,
                            "longitude": restaurant_lon,
                            "website": website if is_valid_website(website) else None,
                            "google_maps_url": google_maps_place_url(
                                restaurant_lat,
                                restaurant_lon,
                            ),
                        },
                        "pairs": [pair],
                    }
                else:
                    restaurant_chargers[dedupe_key]["pairs"].append(pair)

        # Build deduplicated results: best-scoring charger is primary, rest go in other_close_chargers.
        results = []
        for entry in restaurant_chargers.values():
            pairs = sorted(
                entry["pairs"],
                key=lambda p: (
                    p["charger"]["maximum_power_kw"] * _POWER_WEIGHT
                    + (p["charger"].get("reviews", {}).get("rating", _DEFAULT_RATING) * _CHARGER_RATING_WEIGHT)
                    - p["distance"]["straight_line_metres"] * _DISTANCE_COST
                ),
                reverse=True,
            )
            primary = pairs[0]
            primary_charger = primary["charger"]
            result: dict = {
                "restaurant": entry["restaurant"],
                "charger": {
                    k: v
                    for k, v in primary_charger.items()
                    if k not in ("connector_types", "latitude", "longitude", "openchargemap_poi_id")
                },
                "distance": primary["distance"],
            }
            if len(pairs) > 1:
                result["other_close_chargers"] = [
                    {
                        "name": p["charger"]["name"],
                        "openchargemap_url": p["charger"]["openchargemap_url"],
                        "straight_line_metres": p["distance"]["straight_line_metres"],
                        "estimated_walking_time_minutes": p["distance"][
                            "estimated_walking_time_minutes"
                        ],
                        "reviews": p["charger"].get("reviews"),
                    }
                    for p in pairs[1:]
                ]
            results.append(result)

        results.sort(
            key=lambda item: (
                item["distance"]["straight_line_metres"],
                -item["charger"]["maximum_power_kw"],
                item["restaurant"]["name"].lower(),
            )
        )

        results = results[: payload.max_results]

        # Enrich with review data and re-rank using combined score.
        if self._review_provider is not None:
            results = await self._enrich_and_rerank(results)

        warnings: list[str] = []
        if not results:
            warnings.append("No qualifying charger-restaurant pairs were found.")

        return {
            "search": {
                "latitude": payload.latitude,
                "longitude": payload.longitude,
                "radius_km": payload.radius_km,
                "restaurant_radius_m": payload.restaurant_radius_m,
            },
            "search_location": self._best_locality(locality_candidates),
            "results": results,
            "diagnostics": {
                "openchargemap_locations_received": len(locations),
                "qualifying_chargers": len(qualifying_chargers),
                "geoapify_places_received": geoapify_places_received,
                "qualifying_restaurant_charger_pairs": len(results),
                "warnings": warnings,
            },
        }

    @staticmethod
    def _locality_from_station(station: dict[str, Any]) -> str | None:
        address = station.get("AddressInfo") or {}
        town = address.get("Town")
        state = address.get("StateOrProvince")
        country = address.get("Country") or {}
        country_title = country.get("Title") if isinstance(country, dict) else None
        parts = [part for part in [town, state, country_title] if part]
        return ", ".join(parts) if parts else None

    @staticmethod
    def _locality_from_geoapify(properties: dict[str, Any]) -> str | None:
        city = properties.get("city") or properties.get("town") or properties.get("county")
        state = properties.get("state")
        country = properties.get("country")
        parts = [part for part in [city, state, country] if part]
        return ", ".join(parts) if parts else None

    @staticmethod
    def _best_locality(localities: list[str]) -> str | None:
        for locality in localities:
            if locality:
                return locality
        return None

    async def _enrich_and_rerank(self, results: list[dict]) -> list[dict]:
        """Fetch review data for each restaurant in parallel and re-sort by combined score."""

        async def enrich_one(item: dict) -> dict:
            r = item["restaurant"]
            info = await self._review_provider.lookup(r["name"], r["latitude"], r["longitude"])  # type: ignore[union-attr]
            if info is not None:
                item["restaurant"]["reviews"] = {
                    "rating": info.rating,
                    "review_count": info.review_count,
                    "price_level": info.price_level,
                    "cuisine_types": info.cuisine_types,
                    "is_open_now": info.is_open_now,
                    "provider_url": info.provider_url,
                    "provider": info.provider,
                    "is_fast_food": info.is_fast_food,
                }
            return item

        enriched = list(await asyncio.gather(*[enrich_one(item) for item in results]))
        enriched.sort(key=lambda item: (-_combined_score(item), item["restaurant"]["name"].lower()))
        return enriched


def _combined_score(item: dict) -> float:
    """Higher is better.  Balances charger power, restaurant rating, and walking distance."""
    power_kw = item["charger"]["maximum_power_kw"]
    distance_m = item["distance"]["straight_line_metres"]

    charger_reviews = item["charger"].get("reviews")
    charger_rating = charger_reviews["rating"] if charger_reviews else _DEFAULT_RATING

    reviews = item["restaurant"].get("reviews")
    rating = reviews["rating"] if reviews else _DEFAULT_RATING
    is_open_now = reviews.get("is_open_now") if reviews else None
    open_adjustment = _OPEN_BONUS if is_open_now is True else (-_CLOSED_PENALTY if is_open_now is False else 0.0)

    fast_food_penalty = 0.0
    price_bonus = 0.0
    if reviews:
        if reviews.get("is_fast_food"):
            fast_food_penalty = _FAST_FOOD_PENALTY

        price_level = reviews.get("price_level") or "$"
        # Price level bonus: $$ -> 1, $$$ -> 2, $$$$ -> 3
        price_val = len(price_level) - 1
        price_bonus = price_val * _PRICE_BONUS

    return (
        power_kw * _POWER_WEIGHT
        + rating * _RATING_WEIGHT
        + charger_rating * _CHARGER_RATING_WEIGHT
        - distance_m * _DISTANCE_COST
        + open_adjustment
        - fast_food_penalty
        + price_bonus
    )
