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
    FAST_CHARGE_MIN_KW,
    charger_speed_label,
    detect_tesla_restriction,
    google_place_to_geoapify_shape,
    is_fast_food_category,
    is_qualifying_place,
    is_valid_website,
    normalize_connector,
    station_is_explicitly_non_operational,
    station_status_value,
)
from app.services.reviews import ReviewProvider, google_place_open_now, is_google_place_closed
from app.utils.distance import haversine_metres
from app.utils.urls import (
    google_maps_place_url,
    google_maps_place_url_from_id,
    google_maps_walking_url,
    openchargemap_details_url,
    plugshare_google_search_url,
)


logger = logging.getLogger(__name__)

# Combined ranking weights.  Adjust these if the balance feels wrong.
# A 5-star restaurant is worth an extra ~300 m walk or ~60 kW of charger power.
_RATING_WEIGHT = 15.0
_DEFAULT_RATING = 3.0   # used when no review data is available
_POWER_WEIGHT = 0.3     # kW → score points
_DISTANCE_COST = 0.05   # metres → score points lost
_FAST_FOOD_PENALTY = 50.0  # Penalty for fast food restaurants and chains
_PRICE_BONUS = 5.0      # Bonus per price level above "$"
_CHARGER_RATING_WEIGHT = 10.0  # Google charger rating → score points

# Recommendation tiers, best (0) to worst.  The agent recommends "primary" when any
# good option exists, and otherwise offers the fallback tiers as labeled compromises.
_TIER_RANK = {
    "primary": 0,        # fast charger, good restaurant, comfortable walk
    "distant_good": 1,   # fast charger, good restaurant, longer walk
    "slow_charger": 2,   # slow L2 charger, good restaurant
    "fast_food": 3,      # fast charger, fast-food/chain, comfortable walk
    "other": 4,          # anything more compromised (slow+far, far+fast-food, unknown, …)
}

# Cap on how many restaurants are enriched with review data per (speed × near/far)
# bucket.  Bounds review-API calls while guaranteeing far-but-good and slow-charger
# candidates survive to be tiered (rather than being truncated away by distance first).
_ENRICH_PER_BUCKET = 15


class DiningChargerService:
    def __init__(
        self,
        ocm_client: OpenChargeMapClient,
        geo_client: GeoapifyClient,
        review_provider: ReviewProvider | None = None,
        google_client: GooglePlacesClient | None = None,
        restaurant_search_geoapify: bool = True,
        restaurant_search_google: bool = False,
        enable_charger_reviews: bool = True,
        enable_opening_hours: bool = True,
    ) -> None:
        self._ocm_client = ocm_client
        self._geo_client = geo_client
        self._review_provider = review_provider
        self._google_client = google_client
        self._restaurant_search_geoapify = restaurant_search_geoapify
        self._restaurant_search_google = restaurant_search_google
        self._enable_charger_reviews = enable_charger_reviews
        self._enable_opening_hours = enable_opening_hours

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

            name = address.get("Title") or "Unknown Charger"
            city = address.get("Town")
            network = (station.get("OperatorInfo") or {}).get("Title")
            plugshare_url = plugshare_google_search_url(name, city, network)

            qualifying_chargers.append(
                {
                    "name": name,
                    "network": network,
                    "connector_types": sorted(
                        connector_types,
                        key=lambda connector: (connector["type"], -connector["power_kw"]),
                    ),
                    "maximum_power_kw": max(c["power_kw"] for c in connector_types),
                    "charger_speed": charger_speed_label(
                        max(c["power_kw"] for c in connector_types)
                    ),
                    "is_fast_charger": max(c["power_kw"] for c in connector_types)
                    >= FAST_CHARGE_MIN_KW,
                    "latitude": station_lat,
                    "longitude": station_lon,
                    "status": station_status_value(station),
                    "compatibility_notes": compatibility_notes,
                    "openchargemap_poi_id": station_id,
                    "openchargemap_url": openchargemap_details_url(station_id),
                    "plugshare_url": plugshare_url,
                }
            )

        # If OCM returned nothing and Google client is available, fall back to Google Places.
        # Google doesn't provide connector type or power data, so these chargers are added
        # without connector filtering and are marked with source="google_places".
        if not qualifying_chargers and self._google_client:
            logger.info("OCM returned no qualifying chargers — falling back to Google Places search.")
            try:
                google_stations = await self._google_client.search_ev_chargers(
                    latitude=payload.latitude,
                    longitude=payload.longitude,
                    radius_m=int(payload.radius_km * 1000),
                )
                for gstation in google_stations:
                    if is_google_place_closed(gstation):
                        continue
                    loc = (gstation.get("geometry") or {}).get("location") or {}
                    glat = loc.get("lat")
                    glon = loc.get("lng")
                    if glat is None or glon is None:
                        continue
                    gname = gstation.get("name") or "Unknown Charger"
                    plugshare_url = plugshare_google_search_url(gname, None, None)
                    charger: dict[str, Any] = {
                        "name": gname,
                        "network": None,
                        "connector_types": [],
                        "maximum_power_kw": 0,
                        "charger_speed": "UNKNOWN",
                        "is_fast_charger": False,
                        "latitude": glat,
                        "longitude": glon,
                        "status": "Unknown",
                        "compatibility_notes": "Connector type and power level unknown — found via Google Places fallback. Verify before relying on this charger.",
                        "openchargemap_poi_id": None,
                        "openchargemap_url": None,
                        "plugshare_url": plugshare_url,
                        "source": "google_places",
                    }
                    open_now = google_place_open_now(gstation)
                    charger["reviews"] = {
                        "rating": float(gstation.get("rating") or 0.0),
                        "review_count": int(gstation.get("user_ratings_total") or 0),
                        "provider": "google",
                        **({"is_open_now": open_now} if open_now is not None else {}),
                    }
                    qualifying_chargers.append(charger)
                    locality = gstation.get("vicinity")
                    if locality:
                        locality_candidates.append(locality)
            except Exception:
                logger.warning("Google Places fallback charger search failed.", exc_info=True)

        # Try to fetch Google reviews for chargers if enabled.
        if self._google_client and self._enable_charger_reviews:
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
                        reviews: dict[str, Any] = {
                            "rating": float(place.get("rating") or 0.0),
                            "review_count": int(place.get("user_ratings_total") or 0),
                            "provider": "google",
                        }
                        open_now = google_place_open_now(place)
                        if open_now is not None:
                            reviews["is_open_now"] = open_now
                        if "url" in place:
                            reviews["provider_url"] = place["url"]
                        c["reviews"] = reviews
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
        ) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
            """Returns (charger, places, geoapify_raw_count)."""
            all_places: list[dict[str, Any]] = []
            geo_raw_count = 0

            if self._restaurant_search_geoapify:
                geo_places = await self._geo_client.nearby_food_places(
                    latitude=charger_item["latitude"],
                    longitude=charger_item["longitude"],
                    radius_m=payload.restaurant_radius_m,
                )
                geo_raw_count = len(geo_places)
                all_places.extend(geo_places)

            if self._restaurant_search_google and self._google_client:
                try:
                    google_food = await self._google_client.nearby_food_places(
                        latitude=charger_item["latitude"],
                        longitude=charger_item["longitude"],
                        radius_m=payload.restaurant_radius_m,
                    )
                    for gp in google_food:
                        all_places.append(google_place_to_geoapify_shape(gp))
                except Exception:
                    logger.warning("Google Places restaurant search failed.", exc_info=True)

            # Cross-source deduplication by name alone.
            # Within a single charger's restaurant_radius_m search area, two places with
            # identical names from different providers are almost certainly the same business.
            # Geoapify results are listed first so we prefer their richer metadata when both
            # providers return the same place.
            seen_names: set[str] = set()
            deduped: list[dict[str, Any]] = []
            for place in all_places:
                props = place.get("properties") or {}
                name = (props.get("name") or "").strip().lower()
                if name not in seen_names:
                    seen_names.add(name)
                    deduped.append(place)

            return charger_item, deduped, geo_raw_count

        charger_places_pairs = await asyncio.gather(
            *[fetch_places(c) for c in qualifying_chargers]
        )

        for charger, places, geo_raw_count in charger_places_pairs:
            geoapify_places_received += geo_raw_count

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

                # Use name + rounded coordinates (~111 m precision) as the canonical
                # dedup key so that the same restaurant from different sources
                # (Geoapify vs Google Places) or different charger queries is merged
                # into a single entry.
                place_name_lower = (properties.get("name") or "").strip().lower()
                dedupe_key = (
                    f"{place_name_lower}"
                    f"|{round(float(restaurant_lat), 3)}"
                    f"|{round(float(restaurant_lon), 3)}"
                )
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
                        # Category-based fast-food signal, used for tiering when no review
                        # provider is configured (reviews.is_fast_food is authoritative when present).
                        "is_fast_food_category": is_fast_food_category(place),
                        # Genuine Google place_id if this place came from Google restaurant
                        # search; used to build a query_place_id Maps link.
                        "google_place_id": properties.get("google_place_id"),
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
                # Private, popped before returning; feeds tiering when reviews are absent.
                "_is_fast_food_category": entry.get("is_fast_food_category", False),
                # Private, popped before returning; genuine Google place_id when known.
                "_google_place_id": entry.get("google_place_id"),
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

        preferred_radius_m = min(payload.preferred_radius_m, payload.restaurant_radius_m)

        # Select a bounded, diversity-preserving candidate set to enrich with reviews.
        # Stratifying by (charger speed × near/far) guarantees far-but-good and slow-charger
        # candidates survive to be tiered, instead of being truncated away by distance first.
        candidates = self._select_enrichment_candidates(results, preferred_radius_m)

        # Enrich with review data (needed for accurate fast-food detection and scoring).
        if self._review_provider is not None:
            candidates = await self._enrich(candidates)

        # Drop permanently-closed businesses outright — they are never a valid recommendation.
        # (Current "open now" status is deliberately NOT used to exclude, since the user is
        # usually planning a future stop.)
        candidates = [item for item in candidates if not _is_permanently_closed(item)]

        # Classify each candidate into a recommendation tier.
        for item in candidates:
            item["tier"] = _recommendation_tier(item, preferred_radius_m)

        if not payload.include_fast_food:
            candidates = [item for item in candidates if not _item_is_fast_food(item)]

        # Tier-aware ranking: best tier first, combined score within a tier.  When good
        # primaries exist they fill the list; when they don't, the fallback tiers rise to
        # the top and survive truncation.
        candidates.sort(
            key=lambda item: (
                _TIER_RANK.get(item["tier"], _TIER_RANK["other"]),
                -_combined_score(item),
                item["restaurant"]["name"].lower(),
            )
        )
        results = candidates[: payload.max_results]

        # Fetch full weekly opening hours for the final Google-sourced results so the agent
        # can discuss which meals/days a place is closed. Bounded by max_results, and only
        # for results that already have a Google place_id.
        if self._google_client and self._enable_opening_hours:
            await self._attach_opening_hours(results)

        # Finalise each result: upgrade the restaurant's Maps link to a place_id-anchored
        # URL when a genuine Google place_id is known, and drop private helper fields.
        for item in results:
            item.pop("_is_fast_food_category", None)
            place_id = item.pop("_google_place_id", None)
            if place_id:
                item["restaurant"]["google_maps_url"] = google_maps_place_url_from_id(
                    item["restaurant"]["name"], place_id
                )

        tier_counts: dict[str, int] = {}
        for item in results:
            tier_counts[item["tier"]] = tier_counts.get(item["tier"], 0) + 1

        warnings: list[str] = []
        if not results:
            warnings.append("No qualifying charger-restaurant pairs were found.")

        return {
            "search": {
                "latitude": payload.latitude,
                "longitude": payload.longitude,
                "radius_km": payload.radius_km,
                "restaurant_radius_m": payload.restaurant_radius_m,
                "preferred_radius_m": preferred_radius_m,
            },
            "search_location": self._best_locality(locality_candidates),
            "results": results,
            "diagnostics": {
                "openchargemap_locations_received": len(locations),
                "qualifying_chargers": len(qualifying_chargers),
                "geoapify_places_received": geoapify_places_received,
                "qualifying_restaurant_charger_pairs": len(results),
                "tier_counts": tier_counts,
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

    @staticmethod
    def _select_enrichment_candidates(
        results: list[dict], preferred_radius_m: int
    ) -> list[dict]:
        """Pick a bounded set of results to enrich, preserving tier diversity.

        Stratifies by the dimensions known before review lookup — charger speed
        (fast vs. not) and near/far — and keeps the nearest ``_ENRICH_PER_BUCKET``
        from each bucket.  This bounds review-API calls while ensuring far-but-good
        and slow-charger candidates are not truncated away by distance before they
        can be tiered and ranked.
        """
        buckets: dict[tuple[str, str], list[dict]] = {}
        for item in results:
            is_fast = item["charger"].get("charger_speed") == "DC_FAST"
            near = item["distance"]["straight_line_metres"] <= preferred_radius_m
            key = ("fast" if is_fast else "slow", "near" if near else "far")
            buckets.setdefault(key, []).append(item)

        selected: list[dict] = []
        for bucket in buckets.values():
            bucket.sort(key=lambda item: item["distance"]["straight_line_metres"])
            selected.extend(bucket[:_ENRICH_PER_BUCKET])
        return selected

    async def _enrich(self, results: list[dict]) -> list[dict]:
        """Fetch review data for each restaurant in parallel (no re-sorting)."""

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
                    "business_status": info.business_status,
                    "weekday_text": info.weekday_text,
                    "provider_url": info.provider_url,
                    "provider": info.provider,
                    "is_fast_food": info.is_fast_food,
                }
                # A place_id matched by the Google review lookup is authoritative — it
                # identifies the exact business — so prefer it over any search-time id.
                if info.place_id:
                    item["_google_place_id"] = info.place_id
            return item

        return list(await asyncio.gather(*[enrich_one(item) for item in results]))

    async def _attach_opening_hours(self, results: list[dict]) -> None:
        """Fetch weekly opening hours (Place Details) for Google-identified results.

        Attaches ``reviews.weekday_text`` and refines ``reviews.business_status`` in place.
        Failures are logged and left as-is so the response is never blocked by this pass.
        """

        async def fetch_one(item: dict) -> None:
            place_id = item.get("_google_place_id")
            if not place_id:
                return
            try:
                details = await self._google_client.place_details(place_id)  # type: ignore[union-attr]
            except Exception:
                logger.warning("Failed to fetch Google opening hours for %r", item["restaurant"]["name"])
                return
            if not details:
                return
            reviews = item["restaurant"].get("reviews")
            if reviews is None:
                return
            opening_hours = details.get("opening_hours") or {}
            weekday_text = opening_hours.get("weekday_text")
            if isinstance(weekday_text, list) and weekday_text:
                reviews["weekday_text"] = weekday_text
            business_status = details.get("business_status")
            if isinstance(business_status, str):
                reviews["business_status"] = business_status

        await asyncio.gather(*[fetch_one(item) for item in results])


def _is_permanently_closed(item: dict) -> bool:
    reviews = item["restaurant"].get("reviews")
    return bool(reviews) and reviews.get("business_status") == "CLOSED_PERMANENTLY"


def _item_is_fast_food(item: dict) -> bool:
    """Whether a result is fast food.

    Prefers the review provider's classification (which also catches chains); falls
    back to the Geoapify/Google ``catering.fast_food`` category when reviews are absent.
    """
    reviews = item["restaurant"].get("reviews")
    review_ff = bool(reviews.get("is_fast_food")) if reviews else False
    return review_ff or bool(item.get("_is_fast_food_category"))


def _recommendation_tier(item: dict, preferred_radius_m: int) -> str:
    """Classify a charger-restaurant pair into a recommendation tier (see ``_TIER_RANK``)."""
    speed = item["charger"].get("charger_speed")
    is_fast = speed == "DC_FAST"
    is_slow = speed == "L2"
    near = item["distance"]["straight_line_metres"] <= preferred_radius_m
    fast_food = _item_is_fast_food(item)

    if is_fast and not fast_food and near:
        return "primary"
    if is_fast and not fast_food and not near:
        return "distant_good"
    if is_slow and not fast_food:
        return "slow_charger"
    if is_fast and fast_food and near:
        return "fast_food"
    return "other"


def _combined_score(item: dict) -> float:
    """Higher is better.  Balances charger power, restaurant rating, and walking distance."""
    power_kw = item["charger"]["maximum_power_kw"]
    distance_m = item["distance"]["straight_line_metres"]

    charger_reviews = item["charger"].get("reviews")
    charger_rating = charger_reviews["rating"] if charger_reviews else _DEFAULT_RATING

    reviews = item["restaurant"].get("reviews")
    rating = reviews["rating"] if reviews else _DEFAULT_RATING

    # Current open-now status intentionally does NOT affect ranking: users are usually
    # planning a future stop, so transient status is irrelevant. Durable closure is
    # handled by excluding CLOSED_PERMANENTLY results, not by scoring.

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
        - fast_food_penalty
        + price_bonus
    )
