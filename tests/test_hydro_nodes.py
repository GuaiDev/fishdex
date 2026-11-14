"""Int64 node encoding: round-trips, ordering of fields, range limits."""

import random

import pytest

from src.services.hydro_nodes import decode_node, encode_node, encode_node_str

INT64_MAX = 2**63 - 1


@pytest.mark.parametrize(
    "lon,lat",
    [
        (-79.66543, 43.43211),  # Oakville
        (-123.12345, 49.28273),  # BC
        (151.20929, -33.86882),  # southern hemisphere, east
        (0.0, 0.0),
        (-180.0, -90.0),
        (180.0, 90.0),
        (-0.00001, 0.00001),
    ],
)
def test_round_trip(lon, lat):
    assert decode_node(encode_node(lon, lat)) == (lon, lat)


def test_round_trip_random_points():
    rng = random.Random(7)
    for _ in range(5000):
        lon = round(rng.uniform(-180, 180), 5)
        lat = round(rng.uniform(-90, 90), 5)
        node = encode_node(lon, lat)
        assert 0 <= node <= INT64_MAX
        assert decode_node(node) == pytest.approx((lon, lat), abs=1e-9)


def test_rounds_to_five_decimals():
    assert encode_node(-79.1234549, 43.1) == encode_node(-79.12345, 43.1)
    assert encode_node(-79.1234551, 43.1) == encode_node(-79.12346, 43.1)


def test_same_point_same_node_distinct_points_distinct_nodes():
    assert encode_node(-79.5, 43.5) == encode_node(-79.5, 43.5)
    nodes = {encode_node(-79.5 + i * 1e-5, 43.5 + j * 1e-5) for i in range(20) for j in range(20)}
    assert len(nodes) == 400


def test_extremes_fit_int64():
    assert encode_node(180.0, 90.0) <= INT64_MAX
    assert encode_node(-180.0, -90.0) == 0


@pytest.mark.parametrize("lon,lat", [(180.1, 0), (-180.1, 0), (0, 90.1), (0, -90.1)])
def test_out_of_range_rejected(lon, lat):
    with pytest.raises(ValueError):
        encode_node(lon, lat)


def test_nan_rejected():
    with pytest.raises(ValueError):
        encode_node(float("nan"), 0.0)


@pytest.mark.parametrize("bad", [-1, (36_000_001 << 25)])
def test_decode_rejects_non_nodes(bad):
    with pytest.raises(ValueError):
        decode_node(bad)


def test_legacy_string_matches_float_encoding():
    assert encode_node_str("-79.5,43.5") == encode_node(-79.5, 43.5)
    # what hydro_network writes today: f"{round(lon, 5)},{round(lat, 5)}"
    lon, lat = -79.123456789, 43.987654321
    legacy = f"{round(lon, 5)},{round(lat, 5)}"
    assert encode_node_str(legacy) == encode_node(lon, lat)


@pytest.mark.parametrize("bad", ["", "1", "a,b", "1,2,3"])
def test_legacy_string_rejects_garbage(bad):
    with pytest.raises(ValueError):
        encode_node_str(bad)
