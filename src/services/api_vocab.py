"""The spots vocabulary at the API edge.

The database tables, stored JSON and internal modules still say "stretch",
"anchor", "confluence" and "tailwater" — those are the build's own words and
renaming them would mean migrating stored rows. The API does not use them. Every
payload that carries a stored spot or water body goes through here on the way
out, so the UI sees one vocabulary and a retired word cannot leak by someone
forgetting to rename a key in a new endpoint.

`tests/test_api_vocab.py` walks real responses and fails on a retired word in a
field name or a user-facing string.
"""

# structure_attrs keys (stored on the spot) -> the API name.
STRUCTURE_ATTR_KEYS = {
    "stretch_upstream_km": "river_upstream_km",
    "stretch_flows_on": "river_flows_on",
    "tailwater_verified": "below_dam_verified",
}

# structure_type values (stored) -> the API value.
STRUCTURE_TYPES = {"confluence": "junction"}

# water body geometry_source values (stored) -> the API value.
GEOMETRY_SOURCES = {"stretch_union": "section_union"}


def spot_for_api(d: dict) -> dict:
    """A `Spot.model_dump()` in API words. Returns a new dict; the input is untouched."""
    out = dict(d)
    out["structure_type"] = STRUCTURE_TYPES.get(d["structure_type"], d["structure_type"])
    out["structure_attrs"] = {
        STRUCTURE_ATTR_KEYS.get(k, k): v for k, v in d["structure_attrs"].items()
    }
    out["sources"] = [
        {("merged_record_ids" if k == "anchor_ids" else k): v for k, v in s.items()}
        for s in d["sources"]
    ]
    return out


def water_body_for_api(d: dict) -> dict:
    """A `WaterBody.model_dump()` in API words. Returns a new dict."""
    out = {("section_ids" if k == "stretch_ids" else k): v for k, v in d.items()}
    out["geometry_source"] = GEOMETRY_SOURCES.get(d["geometry_source"], d["geometry_source"])
    return out
