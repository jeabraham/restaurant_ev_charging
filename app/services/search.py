from __future__ import annotations

import math
from typing import Any

from app.clients.geoapify import GeoapifyClient
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
from app.utils.distance import haversine_metres
from app.utils.urls import (
    google_maps_place_url,
    google_maps_walking_url,
    openchargemap_details_url,
)


class DiningChargerService:
    def __init__(self, ocm_client: OpenChargeMapClient, geo_client: GeoapifyClient) -> None:
        self._ocm_client = ocm_client
        self._geo_client = geo_client

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
                normalized = normalize_connector(connection, payload.ccs, payload.nacs)
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

        results = []
        for charger in qualifying_chargers:
            places = await self._geo_client.nearby_food_places(
                latitude=charger["latitude"],
                longitude=charger["longitude"],
                radius_m=payload.restaurant_radius_m,
            )
            geoapify_places_received += len(places)

            seen_places: set[str] = set()
            for place in places:
                if not is_qualifying_place(place):
                    continue

                dedupe_key = dedupe_geoapify_place_key(place)
                if dedupe_key in seen_places:
                    continue
                seen_places.add(dedupe_key)

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
                    continue

                locality = self._locality_from_geoapify(properties)
                if locality:
                    locality_candidates.append(locality)

                website = properties.get("website")
                results.append(
                    {
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
                )

        results.sort(
            key=lambda item: (
                item["distance"]["straight_line_metres"],
                -item["charger"]["maximum_power_kw"],
                item["restaurant"]["name"].lower(),
            )
        )

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
