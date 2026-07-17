from app.utils.urls import (
    google_maps_place_url,
    google_maps_walking_url,
    openchargemap_details_url,
    plugshare_google_search_url,
)


def test_openchargemap_details_url():
    assert openchargemap_details_url(123456) == "https://openchargemap.io/poi/details/123456"


def test_plugshare_google_search_url():
    url = plugshare_google_search_url("Walmart Supercenter", "Spokane", "Electrify America")
    assert "https://www.google.com/search?q=" in url
    assert "site%3Aplugshare.com%2Flocation" in url
    assert "Walmart+Supercenter" in url
    assert "Spokane" in url
    assert "Electrify+America" in url


def test_google_maps_urls():
    walking = google_maps_walking_url(51.4672, -109.1571, 51.4668, -109.1549)
    place = google_maps_place_url(51.4668, -109.1549)

    assert "travelmode=walking" in walking
    assert "origin=51.4672%2C-109.1571" in walking
    assert "destination=51.4668%2C-109.1549" in walking
    assert place == "https://www.google.com/maps/search/?api=1&query=51.4668%2C-109.1549"
