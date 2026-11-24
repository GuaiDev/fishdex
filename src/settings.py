"""Deploy-level settings: what an install serves when no user says otherwise.

A signed-in user's jurisdiction and home come from their own row. These values
are only for the cases with no user to ask: a brand-new account with no home yet,
the signed-out demo, and the readiness check.
"""

import os

DEFAULT_JURISDICTION_ENV = "FISHDEX_DEFAULT_JURISDICTION"
DEMO_CENTER_ENV = "FISHDEX_DEMO_CENTER"
DEFAULT_REGION_NAME_ENV = "FISHDEX_DEFAULT_REGION_NAME"

_FALLBACK_JURISDICTION = "CA-ON"
_FALLBACK_CENTER = (-79.7, 43.45)  # (lon, lat)
_FALLBACK_REGION_NAME = "Southern Ontario"


def default_jurisdiction() -> str:
    """ISO 3166-2 code this deploy serves, for anyone with no home jurisdiction."""
    return os.environ.get(DEFAULT_JURISDICTION_ENV, "").strip() or _FALLBACK_JURISDICTION


def demo_center() -> tuple[float, float]:
    """(lon, lat) the signed-out demo map is centred on. Env: "lon,lat"."""
    return _parse_center(os.environ.get(DEMO_CENTER_ENV, "")) or _FALLBACK_CENTER


def default_region() -> dict:
    """A centre for a user with no home, GPS or located trip: {lat, lng, name}."""
    lon, lat = demo_center()
    name = os.environ.get(DEFAULT_REGION_NAME_ENV, "").strip() or _FALLBACK_REGION_NAME
    return {"lat": lat, "lng": lon, "name": name}


def _parse_center(raw: str) -> tuple[float, float] | None:
    try:
        lon, lat = (float(p) for p in raw.split(","))
    except ValueError:
        return None
    return (lon, lat) if -180 <= lon <= 180 and -90 <= lat <= 90 else None
