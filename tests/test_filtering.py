from app.services.filtering import (
    dedupe_geoapify_place_key,
    normalize_connector,
)


def test_power_and_connector_filtering():
    ccs_conn = {"PowerKW": 150, "ConnectionType": {"ID": 33, "Title": "CCS Combo"}}
    nacs_conn = {"PowerKW": 250, "ConnectionType": {"ID": 30, "Title": "Tesla"}}
    slow_conn = {"PowerKW": 22, "ConnectionType": {"ID": 33, "Title": "CCS"}}

    assert normalize_connector(ccs_conn, requested_ccs=True, requested_nacs=False) == {
        "type": "CCS",
        "power_kw": 150,
    }
    assert normalize_connector(nacs_conn, requested_ccs=False, requested_nacs=True) == {
        "type": "NACS",
        "power_kw": 250,
    }
    assert normalize_connector(slow_conn, requested_ccs=True, requested_nacs=False) is None


def test_deduplication_key_fallback():
    place = {
        "properties": {
            "name": " Example Cafe ",
            "formatted": "1 Main Street",
            "lat": 51.4668,
            "lon": -109.1549,
        }
    }
    assert dedupe_geoapify_place_key(place) == "fallback:example cafe|1 main street|51.4668|-109.1549"
