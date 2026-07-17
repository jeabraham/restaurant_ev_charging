from __future__ import annotations

import pytest

from app.services.reviews import (
    ReviewInfo,
    YelpReviewProvider,
    GooglePlacesReviewProvider,
    _parse_review_info,
    _parse_google_place,
)
from app.services.search import _combined_score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _yelp_business(
    *,
    name: str = "Test Restaurant",
    rating: float = 4.5,
    review_count: int = 200,
    price: str | None = "$$",
    is_closed: bool = False,
    url: str = "https://yelp.com/biz/test",
    categories: list[dict] | None = None,
) -> dict:
    return {
        "name": name,
        "rating": rating,
        "review_count": review_count,
        "price": price,
        "is_closed": is_closed,
        "url": url,
        "categories": categories or [{"alias": "restaurants", "title": "Restaurants"}],
    }


def _result_item(
    *,
    power_kw: float = 150.0,
    distance_m: float = 300.0,
    rating: float | None = None,
    is_open_now: bool | None = None,
    name: str = "Test Restaurant",
    price_level: str = "$",
) -> dict:
    reviews = None
    if rating is not None:
        reviews = {
            "rating": rating,
            "review_count": 100,
            "price_level": price_level,
            "cuisine_types": ["Restaurants"],
            "is_open_now": is_open_now,
            "provider_url": "https://yelp.com/biz/test",
            "provider": "yelp",
        }
    return {
        "restaurant": {"name": name, "latitude": 51.0, "longitude": -110.0, "reviews": reviews},
        "charger": {"maximum_power_kw": power_kw},
        "distance": {"straight_line_metres": distance_m},
    }


# ---------------------------------------------------------------------------
# 1. Successful enrichment
# ---------------------------------------------------------------------------

def test_parse_review_info_maps_fields():
    biz = _yelp_business(
        rating=4.2,
        review_count=312,
        price="$$$",
        is_closed=False,
        url="https://yelp.com/biz/bistro",
        categories=[{"alias": "french", "title": "French"}, {"alias": "wine_bars", "title": "Wine Bars"}],
    )
    info = _parse_review_info(biz)

    assert info.rating == 4.2
    assert info.review_count == 312
    assert info.price_level == "$$$"
    assert info.cuisine_types == ["French", "Wine Bars"]
    assert info.is_open_now is True
    assert info.provider_url == "https://yelp.com/biz/bistro"
    assert info.provider == "yelp"


def test_parse_review_info_closed_restaurant():
    biz = _yelp_business(is_closed=True)
    info = _parse_review_info(biz)
    assert info.is_open_now is False


def test_parse_review_info_unknown_open_status():
    biz = _yelp_business()
    del biz["is_closed"]
    info = _parse_review_info(biz)
    assert info.is_open_now is None


# ---------------------------------------------------------------------------
# 2. No match from Yelp
# ---------------------------------------------------------------------------

class _NoMatchYelpClient:
    async def find_business(self, name, latitude, longitude):
        return None


async def test_yelp_provider_no_match_returns_none():
    provider = YelpReviewProvider(_NoMatchYelpClient())
    result = await provider.lookup("Nonexistent Place", 51.0, -110.0)
    assert result is None


# ---------------------------------------------------------------------------
# 3. Provider failure — Yelp raises an exception
# ---------------------------------------------------------------------------

class _FailingYelpClient:
    async def find_business(self, name, latitude, longitude):
        raise RuntimeError("Network error")


async def test_yelp_provider_failure_returns_none_without_raising():
    provider = YelpReviewProvider(_FailingYelpClient())
    # Must NOT propagate the exception
    result = await provider.lookup("Some Restaurant", 51.0, -110.0)
    assert result is None


# ---------------------------------------------------------------------------
# 4. Combined ranking score
# ---------------------------------------------------------------------------

def test_combined_score_higher_rating_wins_over_extra_distance():
    """A 4.5-star restaurant 400 m away should outscore a 3.0-star at 100 m."""
    high_rated_far = _result_item(rating=4.5, distance_m=400.0)
    low_rated_close = _result_item(rating=3.0, distance_m=100.0)

    assert _combined_score(high_rated_far) > _combined_score(low_rated_close)


def test_combined_score_open_now_beats_unknown_status():
    """A restaurant confirmed open should outscore an identical one with unknown status."""
    open_now = _result_item(rating=4.0, is_open_now=True)
    status_unknown = _result_item(rating=4.0, is_open_now=None)

    assert _combined_score(open_now) > _combined_score(status_unknown)


def test_combined_score_closed_penalised():
    """A confirmed-closed restaurant should score lower than one with unknown status."""
    closed = _result_item(rating=4.0, is_open_now=False)
    unknown = _result_item(rating=4.0, is_open_now=None)

    assert _combined_score(closed) < _combined_score(unknown)


def test_combined_score_no_reviews_uses_default_rating():
    """An item with no reviews should score the same as one with the default 3.0 rating."""
    no_reviews = _result_item()  # reviews=None
    default_rating = _result_item(rating=3.0, is_open_now=None)

    assert _combined_score(no_reviews) == pytest.approx(_combined_score(default_rating))


# ---------------------------------------------------------------------------
# 5. Google Places provider
# ---------------------------------------------------------------------------

def _google_place(
    *,
    name: str = "Test Restaurant",
    rating: float = 4.1,
    user_ratings_total: int = 150,
    price_level: int | None = 2,
    open_now: bool | None = True,
    url: str = "https://maps.google.com/?cid=1234",
    types: list[str] | None = None,
) -> dict:
    place: dict = {
        "name": name,
        "rating": rating,
        "user_ratings_total": user_ratings_total,
        "url": url,
        "types": types or ["italian_restaurant", "restaurant", "food", "establishment"],
    }
    if price_level is not None:
        place["price_level"] = price_level
    if open_now is not None:
        place["opening_hours"] = {"open_now": open_now}
    return place


def test_parse_google_place_maps_fields():
    place = _google_place(
        rating=4.3,
        user_ratings_total=220,
        price_level=3,
        open_now=True,
        url="https://maps.google.com/?cid=9999",
        types=["italian_restaurant", "restaurant", "food", "establishment"],
    )
    info = _parse_google_place(place)

    assert info.rating == 4.3
    assert info.review_count == 220
    assert info.price_level == "$$$"
    assert info.is_open_now is True
    assert info.provider_url == "https://maps.google.com/?cid=9999"
    assert info.provider == "google"
    assert "Italian Restaurant" in info.cuisine_types
    assert "Restaurant" not in info.cuisine_types  # generic filtered out


def test_parse_google_place_closed():
    place = _google_place(open_now=False)
    info = _parse_google_place(place)
    assert info.is_open_now is False


def test_parse_google_place_no_opening_hours():
    place = _google_place(open_now=None)
    info = _parse_google_place(place)
    assert info.is_open_now is None


def test_parse_google_place_price_level_mapping():
    for level, symbol in [(1, "$"), (2, "$$"), (3, "$$$"), (4, "$$$$")]:
        place = _google_place(price_level=level)
        assert _parse_google_place(place).price_level == symbol


def test_parse_google_place_no_price_level():
    place = _google_place(price_level=None)
    info = _parse_google_place(place)
    assert info.price_level is None


def test_parse_google_place_constructs_url_if_missing():
    place = {
        "name": "Fancy Bistro",
        "place_id": "ChIJ12345",
        "rating": 4.5,
        "user_ratings_total": 100,
    }
    info = _parse_google_place(place)
    assert info.provider_url == "https://www.google.com/maps/search/?api=1&query=Fancy%20Bistro&query_place_id=ChIJ12345"


class _NoMatchGoogleClient:
    async def find_place(self, name, latitude, longitude):
        return None


async def test_google_provider_no_match_returns_none():
    provider = GooglePlacesReviewProvider(_NoMatchGoogleClient())
    result = await provider.lookup("Nonexistent Place", 51.0, -110.0)
    assert result is None


class _FailingGoogleClient:
    async def find_place(self, name, latitude, longitude):
        raise RuntimeError("Network error")


async def test_google_provider_failure_returns_none_without_raising():
    provider = GooglePlacesReviewProvider(_FailingGoogleClient())
    result = await provider.lookup("Some Restaurant", 51.0, -110.0)
    assert result is None
