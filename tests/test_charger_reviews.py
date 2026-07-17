from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.search import DiningChargerService
from app.schemas import FindDiningChargersRequest
from app.clients.openchargemap import OpenChargeMapClient
from app.clients.geoapify import GeoapifyClient
from app.clients.google_places import GooglePlacesClient

@pytest.mark.asyncio
async def test_find_includes_charger_reviews_if_google_client_provided():
    # Mock OCM client to return one station
    ocm_client = MagicMock(spec=OpenChargeMapClient)
    ocm_client.nearby_stations = AsyncMock(return_value=[
        {
            "ID": 123,
            "AddressInfo": {
                "Title": "Super Charger",
                "Latitude": 45.0,
                "Longitude": -75.0,
            },
            "Connections": [
                {
                    "ConnectionType": {"ID": 33, "Title": "CCS (Type 1)"},
                    "PowerKW": 150
                }
            ],
        }
    ])

    # Mock Geoapify to return one restaurant
    geo_client = MagicMock(spec=GeoapifyClient)
    geo_client.nearby_food_places = AsyncMock(return_value=[
        {
            "properties": {
                "name": "Good Eats",
                "lat": 45.001,
                "lon": -75.001,
                "formatted": "123 Main St",
                "categories": ["catering.restaurant.italian"],
                "place_id": "place1"
            }
        }
    ])

    # Mock Google Places client
    google_client = MagicMock(spec=GooglePlacesClient)
    google_client.find_place = AsyncMock(return_value={
        "name": "Super Charger",
        "rating": 4.8,
        "user_ratings_total": 50,
        "place_id": "google_place_1",
        "business_status": "OPERATIONAL"
    })

    service = DiningChargerService(ocm_client, geo_client, google_client=google_client)
    
    request = FindDiningChargersRequest(
        latitude=45.0,
        longitude=-75.0,
        radius_km=1,
        restaurant_radius_m=500
    )
    
    response = await service.find(request)
    
    # Assertions
    assert len(response["results"]) > 0
    charger_data = response["results"][0]["charger"]
    
    assert "reviews" in charger_data
    assert charger_data["reviews"]["rating"] == 4.8
    assert charger_data["reviews"]["provider"] == "google"

@pytest.mark.asyncio
async def test_charger_rating_affects_ranking():
    # Two chargers:
    # 1. Higher power (150kW) but lower rating (3.0)
    # 2. Lower power (50kW) but higher rating (5.0)
    # Actually, let's use same power but different ratings to make it obvious.
    
    ocm_client = MagicMock(spec=OpenChargeMapClient)
    ocm_client.nearby_stations = AsyncMock(return_value=[
        {
            "ID": 1,
            "AddressInfo": {"Title": "Charger A", "Latitude": 45.0, "Longitude": -75.0},
            "Connections": [{"ConnectionType": {"ID": 33}, "PowerKW": 150}],
        },
        {
            "ID": 2,
            "AddressInfo": {"Title": "Charger B", "Latitude": 45.0, "Longitude": -75.0},
            "Connections": [{"ConnectionType": {"ID": 33}, "PowerKW": 150}],
        }
    ])

    geo_client = MagicMock(spec=GeoapifyClient)
    geo_client.nearby_food_places = AsyncMock(return_value=[
        {
            "properties": {
                "name": "Restaurant Near Both",
                "lat": 45.0,
                "lon": -75.0,
                "formatted": "Street",
                "categories": ["catering.restaurant"],
                "place_id": "res1"
            }
        }
    ])

    google_client = MagicMock(spec=GooglePlacesClient)
    # Return 3.0 for Charger A and 5.0 for Charger B
    async def mock_find_place(name, lat, lon):
        if "Charger A" in name:
            return {"rating": 3.0, "user_ratings_total": 10}
        if "Charger B" in name:
            return {"rating": 5.0, "user_ratings_total": 10}
        return None
    
    google_client.find_place = AsyncMock(side_effect=mock_find_place)

    service = DiningChargerService(ocm_client, geo_client, google_client=google_client)
    
    request = FindDiningChargersRequest(latitude=45.0, longitude=-75.0)
    response = await service.find(request)
    
    # Since they are near the same restaurant, the one with Charger B should be ranked higher
    # Wait, the current logic deduplicates by restaurant.
    # It picks the BEST charger for each restaurant.
    # So for "Restaurant Near Both", it should pick Charger B as the primary charger.
    
    results = response["results"]
    assert len(results) == 1
    assert results[0]["restaurant"]["name"] == "Restaurant Near Both"
    assert results[0]["charger"]["name"] == "Charger B"

@pytest.mark.asyncio
async def test_charger_rating_affects_restaurant_ranking():
    # Two restaurants, each near a different charger.
    # Restaurant A is near Charger A (Rating 5.0)
    # Restaurant B is near Charger B (Rating 3.0)
    # Everything else is equal. Restaurant A should be ranked higher.
    
    ocm_client = MagicMock(spec=OpenChargeMapClient)
    ocm_client.nearby_stations = AsyncMock(return_value=[
        {
            "ID": 1,
            "AddressInfo": {"Title": "Charger A", "Latitude": 45.001, "Longitude": -75.0},
            "Connections": [{"ConnectionType": {"ID": 33}, "PowerKW": 150}],
        },
        {
            "ID": 2,
            "AddressInfo": {"Title": "Charger B", "Latitude": 44.999, "Longitude": -75.0},
            "Connections": [{"ConnectionType": {"ID": 33}, "PowerKW": 150}],
        }
    ])

    geo_client = MagicMock(spec=GeoapifyClient)
    async def mock_nearby_food(latitude, longitude, radius_m):
        if latitude == 45.001:
            return [{
                "properties": {
                    "name": "Restaurant A", "lat": 45.001, "lon": -75.0,
                    "formatted": "A", "categories": ["catering.restaurant"], "place_id": "resA"
                }
            }]
        if latitude == 44.999:
            return [{
                "properties": {
                    "name": "Restaurant B", "lat": 44.999, "lon": -75.0,
                    "formatted": "B", "categories": ["catering.restaurant"], "place_id": "resB"
                }
            }]
        return []
    
    geo_client.nearby_food_places = AsyncMock(side_effect=mock_nearby_food)

    google_client = MagicMock(spec=GooglePlacesClient)
    async def mock_find_place(name, lat, lon):
        if "Charger A" in name:
            # Charger A is better
            return {"rating": 5.0, "user_ratings_total": 10}
        if "Charger B" in name:
            # Charger B is worse
            return {"rating": 3.0, "user_ratings_total": 10}
        return None
    
    google_client.find_place = AsyncMock(side_effect=mock_find_place)

    service = DiningChargerService(ocm_client, geo_client, google_client=google_client)
    
    # Search is in the middle
    request = FindDiningChargersRequest(latitude=45.0, longitude=-75.0, radius_km=10)
    response = await service.find(request)
    
    results = response["results"]
    assert len(results) == 2
    
    # Both restaurants are ~111m from search.
    # Initial sorting by distance might be unstable or use name as tiebreaker.
    # But AFTER _enrich_and_rerank (if review_provider is set), it should use _combined_score.
    
    # Wait, in this test I didn't set review_provider! 
    # So _enrich_and_rerank is NOT called.
    # If _enrich_and_rerank is not called, the final results are still sorted by the initial distance sort.
    
    # Let's add a review_provider to the service in the test.
    review_provider = MagicMock()
    # Return dummy review info for restaurants
    from app.services.reviews import ReviewInfo
    dummy_info = ReviewInfo(
        rating=4.0, review_count=100, price_level="$$", cuisine_types=[],
        is_open_now=True, provider_url="", provider="yelp"
    )
    review_provider.lookup = AsyncMock(return_value=dummy_info)
    
    service._review_provider = review_provider
    
    response = await service.find(request)
    results = response["results"]
    
    # Now Restaurant A should definitely be first
    assert results[0]["restaurant"]["name"] == "Restaurant A"
    assert results[1]["restaurant"]["name"] == "Restaurant B"

@pytest.mark.asyncio
async def test_find_excludes_temporarily_closed_chargers_from_google():
    # Mock OCM client to return one station that OCM thinks is Operational
    ocm_client = MagicMock(spec=OpenChargeMapClient)
    ocm_client.nearby_stations = AsyncMock(return_value=[
        {
            "ID": 384851,
            "AddressInfo": {
                "Title": "ChargeStop Edmonton",
                "Latitude": 53.53509,
                "Longitude": -113.50499,
            },
            "Connections": [
                {
                    "ConnectionType": {"ID": 33, "Title": "CCS (Type 1)"},
                    "PowerKW": 150
                }
            ],
            "StatusType": {"IsOperational": True, "Title": "Operational"}
        }
    ])

    # Mock Geoapify to return one restaurant nearby
    geo_client = MagicMock(spec=GeoapifyClient)
    geo_client.nearby_food_places = AsyncMock(return_value=[
        {
            "properties": {
                "name": "Nearby Restaurant",
                "lat": 53.536,
                "lon": -113.505,
                "formatted": "Street",
                "categories": ["catering.restaurant"],
                "place_id": "res1"
            }
        }
    ])

    # Mock Google Places client to return CLOSED_TEMPORARILY
    google_client = MagicMock(spec=GooglePlacesClient)
    google_client.find_place = AsyncMock(return_value={
        "name": "ChargeStop Edmonton",
        "rating": 4.5,
        "user_ratings_total": 100,
        "place_id": "google_place_1",
        "business_status": "CLOSED_TEMPORARILY"
    })

    service = DiningChargerService(ocm_client, geo_client, google_client=google_client)
    
    request = FindDiningChargersRequest(
        latitude=53.535,
        longitude=-113.505,
        radius_km=1,
        restaurant_radius_m=500
    )
    
    response = await service.find(request)
    
    # Assertions: The charger should be excluded, and thus no results found (as it was the only charger)
    assert response["diagnostics"]["qualifying_chargers"] == 0
    assert len(response["results"]) == 0

@pytest.mark.asyncio
async def test_find_excludes_permanently_closed_chargers_from_google():
    ocm_client = MagicMock(spec=OpenChargeMapClient)
    ocm_client.nearby_stations = AsyncMock(return_value=[
        {
            "ID": 1,
            "AddressInfo": {"Title": "Dead Charger", "Latitude": 53.0, "Longitude": -113.0},
            "Connections": [{"ConnectionType": {"ID": 33}, "PowerKW": 150}],
        }
    ])

    geo_client = MagicMock(spec=GeoapifyClient)
    geo_client.nearby_food_places = AsyncMock(return_value=[
        {"properties": {"name": "R", "lat": 53.0, "lon": -113.0, "categories": ["catering.restaurant"], "place_id": "r1"}}
    ])

    google_client = MagicMock(spec=GooglePlacesClient)
    google_client.find_place = AsyncMock(return_value={
        "name": "Dead Charger",
        "business_status": "CLOSED_PERMANENTLY"
    })

    service = DiningChargerService(ocm_client, geo_client, google_client=google_client)
    response = await service.find(FindDiningChargersRequest(latitude=53.0, longitude=-113.0))
    
    assert response["diagnostics"]["qualifying_chargers"] == 0


@pytest.mark.asyncio
async def test_plugshare_url_is_google_search():
    ocm_client = MagicMock(spec=OpenChargeMapClient)
    ocm_client.nearby_stations = AsyncMock(return_value=[
        {
            "ID": 123,
            "AddressInfo": {
                "Title": "Walmart Supercenter",
                "Town": "Spokane",
                "Latitude": 45.0,
                "Longitude": -75.0,
            },
            "Connections": [{"ConnectionType": {"ID": 33}, "PowerKW": 150}],
            "OperatorInfo": {"Title": "Electrify America"}
        }
    ])

    geo_client = MagicMock(spec=GeoapifyClient)
    geo_client.nearby_food_places = AsyncMock(return_value=[
        {
            "properties": {
                "name": "Eatery",
                "lat": 45.0,
                "lon": -75.0,
                "categories": ["catering.restaurant"],
                "place_id": "p1"
            }
        }
    ])

    service = DiningChargerService(ocm_client, geo_client)
    response = await service.find(FindDiningChargersRequest(latitude=45.0, longitude=-75.0))
    
    charger = response["results"][0]["charger"]
    url = charger["plugshare_url"]
    assert "https://www.google.com/search?q=" in url
    assert "site%3Aplugshare.com%2Flocation" in url
    assert "Walmart+Supercenter" in url
    assert "Spokane" in url
    assert "Electrify+America" in url
