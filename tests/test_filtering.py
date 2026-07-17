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
    # Standard 16 A / 240 V Level 2 charger (common in North America, e.g. Blackfalds, AB)
    standard_16a = {"PowerKW": 3.7, "ConnectionType": {"ID": 1, "Title": "J1772"}}
    # True Level 1 (120 V) — below L2_MIN_KW, rejected
    level_1 = {"PowerKW": 1.4, "ConnectionType": {"ID": 1, "Title": "J1772"}}

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
    # 3.7 kW J1772 — a real-world standard 16 A Level 2 charger, should be accepted
    assert normalize_connector(standard_16a, requested_ccs=False, requested_nacs=False, requested_l2=True) == {
        "type": "J1772",
        "level": "L2",
        "power_kw": 3.7,
    }
    # Level 1 (120 V, ~1.4 kW) — below L2_MIN_KW threshold, rejected
    assert normalize_connector(level_1, requested_ccs=False, requested_nacs=False, requested_l2=True) is None
    # L2 connector but l2 not requested — rejected
    assert normalize_connector(j1772_conn, requested_ccs=False, requested_nacs=False, requested_l2=False) is None


def test_nacs_l2_connector_filtering():
    """Tesla Destination Chargers (NACS, 16–48 kW) qualify as NACS L2 only when BOTH nacs AND l2 are requested."""
    tesla_dest = {"PowerKW": 16, "ConnectionType": {"ID": 30, "Title": "Tesla (Model S/X)"}}
    tesla_dest_48 = {"PowerKW": 48, "ConnectionType": {"ID": 30, "Title": "Tesla (Model S/X)"}}

    # Both nacs and l2 requested — destination charger included
    assert normalize_connector(tesla_dest, requested_ccs=False, requested_nacs=True, requested_l2=True) == {
        "type": "NACS",
        "level": "L2",
        "power_kw": 16,
    }
    assert normalize_connector(tesla_dest_48, requested_ccs=False, requested_nacs=True, requested_l2=True) == {
        "type": "NACS",
        "level": "L2",
        "power_kw": 48,
    }
    # nacs=True but l2=False — user wants fast NACS only, destination charger rejected
    assert normalize_connector(tesla_dest, requested_ccs=False, requested_nacs=True, requested_l2=False) is None
    # nacs not requested — rejected
    assert normalize_connector(tesla_dest, requested_ccs=False, requested_nacs=False, requested_l2=True) is None


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
