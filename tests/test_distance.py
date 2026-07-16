from app.utils.distance import haversine_metres


def test_haversine_metres_expected_distance():
    distance = haversine_metres(51.4672, -109.1571, 51.4668, -109.1549)
    assert 150 <= distance <= 190
