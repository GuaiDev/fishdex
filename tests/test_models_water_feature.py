"""Tests for WaterFeature, AccessPoint and ShoreStructure pydantic models."""

import pytest
from pydantic import ValidationError

from src.models.water_feature import AccessPoint, ShoreStructure, WaterFeature

_BASE_FEATURE = dict(
    osm_id="node/123",
    feature_type="lake",
    name="Test Lake",
    lat=43.7,
    lng=-79.4,
    jurisdiction="CA-ON",
    area_m2=None,
    tags={},
)

_BASE_POINT = dict(
    osm_id="node/789",
    access_type="boat_launch",
    name="Test Launch",
    lat=43.69,
    lng=-79.49,
    jurisdiction="CA-ON",
    tags={"amenity": "boat_ramp"},
)


class TestWaterFeature:
    def test_minimal_valid(self):
        f = WaterFeature(**_BASE_FEATURE)
        assert f.osm_id == "node/123"
        assert f.feature_type == "lake"
        assert f.fetched_at is not None

    def test_unnamed_is_allowed(self):
        f = WaterFeature(**{**_BASE_FEATURE, "name": None, "feature_type": "stream"})
        assert f.name is None

    def test_all_feature_types_valid(self):
        all_types = [
            "lake",
            "river",
            "stream",
            "pond",
            "reservoir",
            "wetland",
            "canal",
            "ditch",
            "drain",
            "bay",
        ]
        for ft in all_types:
            f = WaterFeature(**{**_BASE_FEATURE, "feature_type": ft})
            assert f.feature_type == ft

    def test_invalid_feature_type_rejected(self):
        with pytest.raises(ValidationError):
            WaterFeature(**{**_BASE_FEATURE, "feature_type": "ocean"})

    def test_tags_accepts_arbitrary_dict(self):
        tags = {"natural": "water", "water": "lake", "name": "Heart Lake", "wikidata": "Q123"}
        f = WaterFeature(**{**_BASE_FEATURE, "tags": tags})
        assert f.tags == tags

    def test_area_m2_accepts_float(self):
        f = WaterFeature(**{**_BASE_FEATURE, "area_m2": 8800.5})
        assert f.area_m2 == pytest.approx(8800.5)

    def test_area_m2_none_allowed(self):
        f = WaterFeature(**_BASE_FEATURE)
        assert f.area_m2 is None

    def test_way_osm_id_format(self):
        f = WaterFeature(**{**_BASE_FEATURE, "osm_id": "way/456789"})
        assert f.osm_id == "way/456789"


class TestAccessPoint:
    def test_minimal_valid(self):
        p = AccessPoint(**_BASE_POINT)
        assert p.osm_id == "node/789"
        assert p.access_type == "boat_launch"
        assert p.fetched_at is not None

    def test_all_access_types_valid(self):
        all_types = [
            "boat_launch",
            "parking",
            "trail_head",
            "fishing_spot",
            "public_land",
            "conservation_area",
            "park",
            "road",
            "building",
        ]
        for at in all_types:
            p = AccessPoint(**{**_BASE_POINT, "access_type": at})
            assert p.access_type == at

    def test_invalid_access_type_rejected(self):
        with pytest.raises(ValidationError):
            AccessPoint(**{**_BASE_POINT, "access_type": "marina"})

    def test_unnamed_allowed(self):
        p = AccessPoint(**{**_BASE_POINT, "name": None})
        assert p.name is None

    def test_tags_accepts_arbitrary_dict(self):
        tags = {"amenity": "boat_ramp", "access": "public", "fee": "no"}
        p = AccessPoint(**{**_BASE_POINT, "tags": tags})
        assert p.tags == tags


_BASE_STRUCTURE = dict(
    osm_id="way/33656131",
    kind="pier",
    name="Bronte Outer Harbour Marina",
    geometry={"type": "LineString", "coordinates": [[-79.7057, 43.3934], [-79.7062, 43.3937]]},
    lat=43.3948,
    lng=-79.7046,
    jurisdiction="CA-ON",
    tags={"man_made": "pier", "name": "Bronte Outer Harbour Marina"},
)


class TestShoreStructure:
    def test_minimal_valid(self):
        s = ShoreStructure(**_BASE_STRUCTURE)
        assert s.osm_id == "way/33656131"
        assert s.kind == "pier"
        assert s.geometry["type"] == "LineString"
        assert s.fetched_at is not None

    def test_all_kinds_valid(self):
        for kind in ("pier", "breakwater", "harbour"):
            s = ShoreStructure(**{**_BASE_STRUCTURE, "kind": kind})
            assert s.kind == kind

    def test_invalid_kind_rejected(self):
        with pytest.raises(ValidationError):
            ShoreStructure(**{**_BASE_STRUCTURE, "kind": "marina"})

    def test_unnamed_is_allowed(self):
        s = ShoreStructure(**{**_BASE_STRUCTURE, "name": None})
        assert s.name is None

    def test_a_point_geometry_is_allowed(self):
        s = ShoreStructure(
            **{
                **_BASE_STRUCTURE,
                "kind": "harbour",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            }
        )
        assert s.geometry["type"] == "Point"
