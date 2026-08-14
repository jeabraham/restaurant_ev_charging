"""Regression tests for the Kamloops "hidden gem" failure.

These tests use fake clients, so they run without API keys and are the CI guard
for the bug below.  ``tests/test_kamloops_integration.py`` covers the same
scenario against the live APIs.

Background
----------
The agent was asked for a place to eat near a fast charger in Kamloops, BC and
never surfaced **Heaven Sushi Sake Bar** (4.9/5, 629 reviews), ~132 m from the
7Charge charger at 1120 Rogers Way.  It only appeared once the user named both
the restaurant and the charger.

Root cause
----------
The restaurant's rating never reached the ranking stage:

1. ``GooglePlacesClient.nearby_food_places`` already returns ``rating`` and
   ``user_ratings_total`` (they are in ``_NEARBY_FOOD_FIELDS``), but
   ``google_place_to_geoapify_shape`` discarded them.
2. Ratings were therefore only recovered by the bounded Find Place enrichment
   pass, and ``_select_enrichment_candidates`` kept the nearest
   ``_ENRICH_PER_BUCKET`` (15) candidates per bucket **sorted by distance
   alone**.  In a city the size of Kamloops those slots are consumed by places
   10-40 m from some charger, so a gem at 132 m was never enriched.
3. Unenriched results score with ``_DEFAULT_RATING`` (3.0), which costs a 4.9
   restaurant ~47 points — more than the whole distance budget of the
   ``primary`` tier.
4. ``max_results`` then truncated it away entirely.

The fix seeds ``restaurant.reviews`` from the Google search result itself (no
extra API calls), ranks the enrichment budget by combined score rather than raw
distance, and protects the top-rated results from truncation.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.schemas import FindDiningChargersRequest
from app.services.filtering import google_place_to_geoapify_shape
from app.services.reviews import ReviewInfo
from app.services.search import DiningChargerService

# The real pair this test is modelled on.
_HEAVEN_SUSHI = "Heaven Sushi Sake Bar"
_HEAVEN_SUSHI_RATING = 4.9
_HEAVEN_SUSHI_REVIEWS = 629
_SEVEN_CHARGE = "7Charge - Kamloops 37774A"

# Aberdeen area of Kamloops, BC.  The charger sits ~132 m north of the restaurant;
# only the separation matters for these tests, not the absolute position.
_SEVEN_CHARGE_LAT = 50.6264
_SEVEN_CHARGE_LON = -120.2879
_HEAVEN_SUSHI_LAT = 50.6252
_HEAVEN_SUSHI_LON = -120.2879

# Other DC fast chargers scattered across town, each surrounded by closer but
# more ordinary restaurants — the "high-volume result set" the gem was lost in.
_OTHER_CHARGERS = [(50.65 + 0.01 * i, -120.35 - 0.01 * i) for i in range(6)]
_DINERS_PER_CHARGER = 5
_DINER_RATING = 4.1


# ---------------------------------------------------------------------------
# Fake clients
# ---------------------------------------------------------------------------


def _ocm_station(station_id: int, lat: float, lon: float, title: str) -> dict[str, Any]:
    return {
        "ID": station_id,
        "AddressInfo": {
            "Title": title,
            "Latitude": lat,
            "Longitude": lon,
            "Town": "Kamloops",
            "StateOrProvince": "British Columbia",
        },
        "Connections": [{"ConnectionType": {"ID": 33, "Title": "CCS"}, "PowerKW": 150}],
        "StatusType": {"IsOperational": True},
        "OperatorInfo": {"Title": "7Charge"},
    }


def _google_place(
    name: str,
    lat: float,
    lon: float,
    rating: float | None,
    review_count: int,
    types: tuple[str, ...] = ("restaurant", "food"),
) -> dict[str, Any]:
    place: dict[str, Any] = {
        "place_id": f"pid-{name}",
        "name": name,
        "geometry": {"location": {"lat": lat, "lng": lon}},
        "user_ratings_total": review_count,
        "types": list(types),
        "business_status": "OPERATIONAL",
        "vicinity": "Kamloops, BC",
    }
    if rating is not None:
        place["rating"] = rating
    return place


class FakeOcmClient:
    """Returns the 7Charge station plus several other Kamloops fast chargers."""

    async def nearby_stations(
        self, latitude: float, longitude: float, radius_km: float
    ) -> list[dict[str, Any]]:
        stations = [
            _ocm_station(1000 + i, lat, lon, f"Kamloops Charger {i}")
            for i, (lat, lon) in enumerate(_OTHER_CHARGERS)
        ]
        stations.append(
            _ocm_station(1099, _SEVEN_CHARGE_LAT, _SEVEN_CHARGE_LON, _SEVEN_CHARGE)
        )
        return stations


class FakeGeoapifyClient:
    """Geoapify has no catering coverage here, so Google supplies the restaurants."""

    async def nearby_food_places(
        self, latitude: float, longitude: float, radius_m: int
    ) -> list[dict[str, Any]]:
        return []


class FakeGoogleClient:
    """Nearby search returns ratings; Find Place returns nothing (records calls)."""

    def __init__(self, extra_places: list[dict[str, Any]] | None = None) -> None:
        self.find_place_calls: list[str] = []
        self._extra_places = extra_places or []

    async def nearby_food_places(
        self, latitude: float, longitude: float, radius_m: int, max_pages: int = 3
    ) -> list[dict[str, Any]]:
        places = [
            _google_place(
                f"Diner {latitude:.3f}-{index}",
                # ~11 m per 0.0001 degree of latitude: these sit 11-55 m out.
                latitude + 0.0001 * (index + 1),
                longitude,
                _DINER_RATING,
                300,
            )
            for index in range(_DINERS_PER_CHARGER)
        ]
        if abs(latitude - _SEVEN_CHARGE_LAT) < 1e-6:
            places.append(
                _google_place(
                    _HEAVEN_SUSHI,
                    _HEAVEN_SUSHI_LAT,
                    _HEAVEN_SUSHI_LON,
                    _HEAVEN_SUSHI_RATING,
                    _HEAVEN_SUSHI_REVIEWS,
                )
            )
            places.extend(self._extra_places)
        return places

    async def find_place(
        self, name: str, latitude: float, longitude: float
    ) -> dict[str, Any] | None:
        self.find_place_calls.append(name)
        return None

    async def place_details(self, place_id: str) -> dict[str, Any] | None:
        return None


class FakeReviewProvider:
    """Stand-in for the Google/Yelp review provider; records what was looked up."""

    def __init__(self, ratings: dict[str, float]) -> None:
        self.ratings = ratings
        self.looked_up: list[str] = []

    async def lookup(self, name: str, latitude: float, longitude: float) -> ReviewInfo:
        self.looked_up.append(name)
        return ReviewInfo(
            rating=self.ratings.get(name, _DINER_RATING),
            review_count=300,
            price_level="$$",
            cuisine_types=["Sushi"],
            is_open_now=None,
            provider_url="https://example.test/place",
            provider="google",
        )


class MissingMatchReviewProvider:
    """A provider that finds no matching business (a lookup miss or API failure)."""

    def __init__(self) -> None:
        self.looked_up: list[str] = []

    async def lookup(self, name: str, latitude: float, longitude: float) -> None:
        self.looked_up.append(name)
        return None


class UnratedMatchReviewProvider:
    """A provider whose match carries no ratings — e.g. a different, newer branch."""

    def __init__(self) -> None:
        self.looked_up: list[str] = []

    async def lookup(self, name: str, latitude: float, longitude: float) -> ReviewInfo:
        self.looked_up.append(name)
        return ReviewInfo(
            rating=0.0,
            review_count=0,
            price_level="$$",
            cuisine_types=["Sushi"],
            is_open_now=None,
            provider_url="https://example.test/place",
            provider="google",
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _service(
    google_client: FakeGoogleClient,
    review_provider: Any | None = None,
) -> DiningChargerService:
    return DiningChargerService(
        FakeOcmClient(),
        FakeGeoapifyClient(),
        review_provider=review_provider,
        google_client=google_client,
        restaurant_search_geoapify=False,
        restaurant_search_google=True,
        enable_charger_reviews=False,
        enable_opening_hours=False,
    )


def _agent_request(**overrides: Any) -> FindDiningChargersRequest:
    """The parameters the Gemini agent actually sends (see gemini_instructions.md)."""
    params: dict[str, Any] = {
        "latitude": 50.6745,
        "longitude": -120.3273,
        "radius_km": 10,
        "restaurant_radius_m": 2000,
        "max_results": 15,
    }
    params.update(overrides)
    return FindDiningChargersRequest(**params)


def _named(results: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    lowered = name.lower()
    for item in results:
        if lowered in item["restaurant"]["name"].lower():
            return item
    return None


# ---------------------------------------------------------------------------
# Shape conversion — the rating must survive the Google -> Geoapify conversion
# ---------------------------------------------------------------------------


def test_google_shape_conversion_carries_rating_and_review_count():
    """Nearby Search already pays for the rating; it must not be discarded."""
    shaped = google_place_to_geoapify_shape(
        _google_place(_HEAVEN_SUSHI, _HEAVEN_SUSHI_LAT, _HEAVEN_SUSHI_LON, 4.9, 629)
    )

    properties = shaped["properties"]
    assert properties["google_rating"] == 4.9
    assert properties["google_user_ratings_total"] == 629
    assert properties["google_business_status"] == "OPERATIONAL"
    assert "restaurant" in properties["google_types"]


def test_google_shape_conversion_handles_unrated_place():
    """A place Google has no rating for carries no rating, not a zero."""
    shaped = google_place_to_geoapify_shape(
        _google_place("Brand New Cafe", 50.0, -120.0, None, 0)
    )

    properties = shaped["properties"]
    assert properties["google_rating"] is None
    assert properties["google_user_ratings_total"] == 0


# ---------------------------------------------------------------------------
# End-to-end regression — the failure the user reported
# ---------------------------------------------------------------------------


async def test_high_rated_restaurant_survives_a_busy_search_area():
    """A 4.9 at 132 m must be returned, even when many 4.1s sit 11-55 m out.

    This is the Kamloops failure: without the fix every result came back with no
    rating at all, ranking collapsed to pure distance, and all 15 slots went to
    closer but ordinary restaurants.
    """
    google = FakeGoogleClient()
    data = await _service(google).find(_agent_request())

    results = data["results"]
    names = [item["restaurant"]["name"] for item in results]
    heaven = _named(results, _HEAVEN_SUSHI)

    assert heaven is not None, (
        f"{_HEAVEN_SUSHI!r} missing from {len(results)} results — this is the "
        f"reported Kamloops failure.  Returned: {names}"
    )
    assert heaven["charger"]["name"] == _SEVEN_CHARGE
    assert heaven["tier"] == "primary"


async def test_high_rated_restaurant_carries_its_real_rating():
    """The agent can only prefer a 4.9 if the response says 4.9.

    Ratings come from the nearby search itself, so they are present for every
    Google-sourced restaurant — not just the bounded enrichment subset.
    """
    google = FakeGoogleClient()
    data = await _service(google).find(_agent_request())

    heaven = _named(data["results"], _HEAVEN_SUSHI)
    assert heaven is not None
    reviews = heaven["restaurant"].get("reviews")
    assert reviews is not None, (
        f"{_HEAVEN_SUSHI!r} has no reviews field, so the agent cannot tell it is "
        "highly rated"
    )
    assert reviews["rating"] == _HEAVEN_SUSHI_RATING
    assert reviews["review_count"] == _HEAVEN_SUSHI_REVIEWS


async def test_high_rated_restaurant_outranks_closer_ordinary_ones():
    """Ranking must put the 4.9 ahead of the 4.1s once its rating is known."""
    google = FakeGoogleClient()
    data = await _service(google).find(_agent_request())

    names = [item["restaurant"]["name"] for item in data["results"]]
    assert _HEAVEN_SUSHI in names
    position = names.index(_HEAVEN_SUSHI)
    assert position < 5, (
        f"{_HEAVEN_SUSHI!r} ranked {position + 1} of {len(names)}; a 4.9 should be "
        f"near the top of a field of 4.1s.  Order: {names}"
    )


async def test_unrated_restaurant_gets_no_fabricated_reviews():
    """A place Google has no rating for must not be reported as rated 0.0.

    A zero would sink it in ranking and trip the agent's "rating >= 3.5" check,
    which is worse than simply having no review data.
    """
    unrated = _google_place("Brand New Cafe", _SEVEN_CHARGE_LAT + 0.0002, _SEVEN_CHARGE_LON, None, 0)
    google = FakeGoogleClient(extra_places=[unrated])
    # Large enough to hold every pair, so this asserts on the reviews field and not on
    # where an unrated place happens to rank.
    data = await _service(google).find(_agent_request(max_results=100))

    cafe = _named(data["results"], "Brand New Cafe")
    assert cafe is not None, "Unrated places should still be returned"
    assert cafe["restaurant"].get("reviews") is None, (
        "An unrated place must have no reviews field rather than a 0.0 rating"
    )


# ---------------------------------------------------------------------------
# Enrichment budget — spend it on the best candidates, not merely the closest
# ---------------------------------------------------------------------------


async def test_enrichment_budget_reaches_the_highest_rated_candidate():
    """The bounded review lookup must cover the 4.9, not just the nearest places.

    ``_ENRICH_PER_BUCKET`` caps how many restaurants get a review-API call.  When
    that subset was chosen by raw distance, the gem at 132 m lost every slot to
    closer, lower-rated places.
    """
    google = FakeGoogleClient()
    provider = FakeReviewProvider({_HEAVEN_SUSHI: _HEAVEN_SUSHI_RATING})
    data = await _service(google, review_provider=provider).find(_agent_request())

    assert _HEAVEN_SUSHI in provider.looked_up, (
        f"{_HEAVEN_SUSHI!r} never got a review lookup; the enrichment budget went "
        f"to closer but lower-rated places.  Looked up: {provider.looked_up}"
    )
    heaven = _named(data["results"], _HEAVEN_SUSHI)
    assert heaven is not None
    assert heaven["restaurant"]["reviews"]["rating"] == _HEAVEN_SUSHI_RATING


async def test_enrichment_does_not_discard_a_seeded_rating():
    """A review lookup that finds no match must not erase the search-time rating."""
    google = FakeGoogleClient()
    provider = MissingMatchReviewProvider()
    data = await _service(google, review_provider=provider).find(_agent_request())

    assert provider.looked_up, "Expected the enrichment pass to run"
    heaven = _named(data["results"], _HEAVEN_SUSHI)
    assert heaven is not None
    assert heaven["restaurant"]["reviews"]["rating"] == _HEAVEN_SUSHI_RATING


async def test_enrichment_does_not_discard_a_seeded_rating_for_an_unrated_match():
    """A lookup that matches a business with no ratings must not zero out the seed."""
    google = FakeGoogleClient()
    provider = UnratedMatchReviewProvider()
    data = await _service(google, review_provider=provider).find(_agent_request())

    heaven = _named(data["results"], _HEAVEN_SUSHI)
    assert heaven is not None
    reviews = heaven["restaurant"]["reviews"]
    assert reviews["rating"] == _HEAVEN_SUSHI_RATING
    assert reviews["review_count"] == _HEAVEN_SUSHI_REVIEWS
    # The lookup still contributes what it does know.
    assert reviews["price_level"] == "$$"


# ---------------------------------------------------------------------------
# Truncation — a top-rated result must never be cut away silently
# ---------------------------------------------------------------------------


async def test_top_rated_result_is_never_truncated_away():
    """Even at a small max_results, the best-rated option has to come back."""
    google = FakeGoogleClient()
    data = await _service(google).find(_agent_request(max_results=3))

    results = data["results"]
    assert len(results) <= 3 + 3, "Truncation protection must stay bounded"
    assert _named(results, _HEAVEN_SUSHI) is not None, (
        f"{_HEAVEN_SUSHI!r} was truncated away at max_results=3.  Returned: "
        f"{[item['restaurant']['name'] for item in results]}"
    )


async def test_diagnostics_report_protected_results():
    """Forced inclusions are visible in diagnostics rather than silent."""
    google = FakeGoogleClient()
    data = await _service(google).find(_agent_request(max_results=3))

    assert "protected_top_rated" in data["diagnostics"]
    assert isinstance(data["diagnostics"]["protected_top_rated"], int)


@pytest.mark.parametrize("max_results", [1, 3, 15, 30])
async def test_high_rated_restaurant_returned_at_every_result_cap(max_results: int):
    """The gem must appear whatever max_results the agent happens to send."""
    google = FakeGoogleClient()
    data = await _service(google).find(_agent_request(max_results=max_results))

    assert _named(data["results"], _HEAVEN_SUSHI) is not None, (
        f"{_HEAVEN_SUSHI!r} missing at max_results={max_results}.  Returned: "
        f"{[item['restaurant']['name'] for item in data['results']]}"
    )
