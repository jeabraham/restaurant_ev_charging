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
        "level": "DC_FAST",
        "power_kw": 150,
    }
    assert normalize_connector(nacs_conn, requested_ccs=False, requested_nacs=True) == {
        "type": "NACS",
        "level": "DC_FAST",
        "power_kw": 250,
    }
    # 22 kW CCS is not a DC fast charger and not a recognised L2 type ID — rejected
    assert normalize_connector(slow_conn, requested_ccs=True, requested_nacs=False) is None


def test_l2_connector_filtering():
    j1772_conn = {"PowerKW": 11, "ConnectionType": {"ID": 1, "Title": "J1772"}}
    type2_conn = {"PowerKW": 22, "ConnectionType": {"ID": 25, "Title": "IEC 62196 Type 2"}}
    too_slow = {"PowerKW": 3, "ConnectionType": {"ID": 1, "Title": "J1772"}}

    assert normalize_connector(j1772_conn, requested_ccs=False, requested_nacs=False, requested_l2=True) == {
        "type": "J1772",
        "level": "L2",
        "power_kw": 11,
    }
    assert normalize_connector(type2_conn, requested_ccs=False, requested_nacs=False, requested_l2=True) == {
        "type": "Type2",
        "level": "L2",
        "power_kw": 22,
    }
    # Below L2_MIN_KW threshold — rejected
    assert normalize_connector(too_slow, requested_ccs=False, requested_nacs=False, requested_l2=True) is None
    # L2 connector but l2 not requested — rejected
    assert normalize_connector(j1772_conn, requested_ccs=False, requested_nacs=False, requested_l2=False) is None


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
