from __future__ import annotations

from app.services.search import _item_is_fast_food, _recommendation_tier


def _item(*, speed: str, distance: float, reviews: dict | None = None, ff_category: bool = False):
    item: dict = {
        "charger": {"charger_speed": speed},
        "distance": {"straight_line_metres": distance},
        "restaurant": {},
        "_is_fast_food_category": ff_category,
    }
    if reviews is not None:
        item["restaurant"]["reviews"] = reviews
    return item


def test_item_is_fast_food_prefers_reviews_but_falls_back_to_category():
    # Reviews say fast food -> fast food
    assert _item_is_fast_food(_item(speed="DC_FAST", distance=100, reviews={"is_fast_food": True}))
    # Reviews say not fast food, but category does -> still fast food
    assert _item_is_fast_food(
        _item(speed="DC_FAST", distance=100, reviews={"is_fast_food": False}, ff_category=True)
    )
    # No reviews, category flag only
    assert _item_is_fast_food(_item(speed="DC_FAST", distance=100, ff_category=True))
    # Neither -> not fast food
    assert not _item_is_fast_food(_item(speed="DC_FAST", distance=100, reviews={"is_fast_food": False}))
    assert not _item_is_fast_food(_item(speed="DC_FAST", distance=100))


def test_recommendation_tier_primary_and_distant_good():
    preferred = 800
    assert _recommendation_tier(_item(speed="DC_FAST", distance=300), preferred) == "primary"
    assert _recommendation_tier(_item(speed="DC_FAST", distance=800), preferred) == "primary"
    assert _recommendation_tier(_item(speed="DC_FAST", distance=1500), preferred) == "distant_good"


def test_recommendation_tier_slow_charger():
    preferred = 800
    assert _recommendation_tier(_item(speed="L2", distance=200), preferred) == "slow_charger"
    # A slow charger that is also far is still slow_charger (not fast food)
    assert _recommendation_tier(_item(speed="L2", distance=1800), preferred) == "slow_charger"


def test_recommendation_tier_fast_food():
    preferred = 800
    ff = {"is_fast_food": True}
    assert _recommendation_tier(_item(speed="DC_FAST", distance=200, reviews=ff), preferred) == "fast_food"
    # Fast food that is far falls to "other" (double compromise)
    assert _recommendation_tier(_item(speed="DC_FAST", distance=1500, reviews=ff), preferred) == "other"


def test_recommendation_tier_other():
    preferred = 800
    ff = {"is_fast_food": True}
    # Slow + fast food
    assert _recommendation_tier(_item(speed="L2", distance=200, reviews=ff), preferred) == "other"
    # Unknown speed charger
    assert _recommendation_tier(_item(speed="UNKNOWN", distance=200), preferred) == "other"
