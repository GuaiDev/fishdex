"""Parse natural language fishing trip descriptions into structured data.

Uses Claude to extract fields, then resolves the location through a four-layer
pipeline (name match → landmark anchor → Nominatim geocode → unresolved) before
snapping to the nearest OHN segment centroid.
"""

import difflib
import json
import logging
import math
import re
import time
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from src import llm

logger = logging.getLogger(__name__)

_FEATURE_MATRIX_PATH = Path("data/processed/sdm_feature_matrix.parquet")
_SNAP_RADIUS_KM = 5.0  # generous radius for user-described locations

_SYSTEM_PROMPT = """\
You are a fishing trip parser. Extract structured data from natural language fishing
trip descriptions. Always respond with valid JSON only. If information is not mentioned,
use null.

SPECIES EXTRACTION — STRICT RULES:

You are extracting what the user explicitly said they caught. You are not a fishing
guide, you are a transcription tool. Follow these rules without exception:

1. ONLY include species the user explicitly named in their message.
   - "caught a carp" → ["common carp"]
   - "caught redhorse" → ["redhorse sp. (unidentified)"]
   - "caught shorthead redhorse" → ["shorthead redhorse"]

2. If the user names a group but not a species, use "unidentified [group] sp."
   - "shiners" → ["unidentified shiner sp."]
   - "chubs" → ["unidentified chub sp."]
   - "suckers" → ["unidentified sucker sp."]
   - "panfish" → ["unidentified panfish sp."]
   - "catfish" → ["unidentified catfish sp."]

3. If the user expresses uncertainty, flag it:
   - "I think it was a smallmouth" → ["smallmouth bass (uncertain)"]
   - "probably a golden redhorse" → ["golden redhorse (uncertain)"]

4. NEVER add species based on:
   - What you know lives in that river or lake
   - What species are common in that region
   - What species were caught there historically
   - iNaturalist, GBIF, or any other observation database
   - What makes ecological sense for that habitat

5. NEVER add species the user only observed but did not catch, unless the user
   explicitly says "observed" or "saw" AND there is a separate species_observed field.

6. If the user caught nothing, species_caught must be an empty list [].

7. Do not infer, guess, suggest, or helpfully fill in any species information.
   Only transcribe what the user wrote.\
"""

_USER_PROMPT_TEMPLATE = """Parse this fishing trip log: {text}

Return JSON with these fields:
{{
  "date": "YYYY-MM-DD or null",
  "location_description": "string",
  "waterbody_name": "string or null",
  "lat": null,
  "lng": null,
  "species_caught": ["list of species explicitly named by the user — see STRICT RULES"],
  "species_observed": ["species user saw but did not catch"],
  "species_targeted": "string or null",
  "conditions": {{
    "water_level": "low/normal/high/null",
    "water_clarity": "clear/stained/turbid/null",
    "water_temp_c": null,
    "weather": "string or null",
    "flow_trend": "rising/falling/stable/null"
  }},
  "habitat_notes": "string or null",
  "spot_type": "riffle/pool/confluence/culvert/beaver_dam/pond/lake/other/null",
  "fish_count": null,
  "was_productive": null,
  "gear": "string or null",
  "notes": "string or null"
}}"""

# ── species validation ────────────────────────────────────────────────────────


def _build_known_species(db: Any, jurisdiction: str = "CA-ON") -> dict[str, str]:
    """Map lowercased common name -> canonical scientific name for every
    species real to the region.

    Unions species_ranges (the authoritative regional pool) with
    species_mapping.py's broader COMMON_TO_SCIENTIFIC (covers microfishing
    targets not yet backfilled into species_ranges). Aliased taxa (e.g. the
    Largemouth Bass Northern/Florida split) collapse to one canonical name —
    see species_family.py — so this can't be used to validate a species into
    a confusing duplicate label.
    """
    from src.services.species_family import canonical_scientific_name
    from src.services.species_mapping import COMMON_TO_SCIENTIFIC
    from src.storage.species_ranges import query_deduped_species_ranges_for_jurisdiction

    known: dict[str, str] = {}
    try:
        for sr in query_deduped_species_ranges_for_jurisdiction(db, jurisdiction):
            if sr.scientific_name:
                known[sr.species.lower()] = canonical_scientific_name(sr.scientific_name)
    except Exception as e:
        logger.warning("Failed to load species_ranges for parser grounding: %s", e)
    for common, sci in COMMON_TO_SCIENTIFIC.items():
        known.setdefault(common, canonical_scientific_name(sci))
    return known


def _validate_species(
    species_list: list, original_text: str, known_species: dict[str, str] | None = None
) -> list:
    """Remove any species not traceable to words in the user's text.

    If known_species is given (built by _build_known_species), a species that
    IS traceable to the user's text but doesn't resolve to any real regional
    species is not dropped — it's flagged "(unresolved)" so the catch is kept
    but surfaced for human confirmation rather than committed as an invented
    species name.
    """
    from src.services.species_mapping import common_to_scientific

    text_lower = original_text.lower()
    validated = []
    for species in species_list:
        if "unidentified" in species.lower():
            validated.append(species)
            continue
        clean = species.lower().replace("(uncertain)", "").strip()
        words = [w for w in clean.split() if len(w) > 3]
        if not any(w in text_lower for w in words):
            print(f"[VALIDATION REMOVED] '{species}' not found in user text — skipped")
            continue
        if (
            known_species is not None
            and clean not in known_species
            and common_to_scientific(clean) is None
        ):
            print(
                f"[VALIDATION UNRESOLVED] '{species}' not a recognized regional species — flagged"
            )
            validated.append(
                species if "(unresolved)" in species.lower() else f"{species} (unresolved)"
            )
            continue
        validated.append(species)
    return validated


# ── location resolution ───────────────────────────────────────────────────────

_WATERBODY_RE = re.compile(
    r"\b([A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+)*)\s+"
    r"(Creek|River|Brook|Stream|Run|Branch|Channel|Drain|Ditch|Tributary)\b",
    re.IGNORECASE,
)
_LAKE_RE = re.compile(r"\bLake\s+([A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+)*)\b", re.IGNORECASE)

_LANDMARK_KEYWORDS = {
    "bridge",
    "dam",
    "beaver dam",
    "confluence",
    "falls",
    "weir",
    "culvert",
    "crossing",
    "pool below",
    "riffle at",
    "upstream of",
    "below the",
    "above the",
}


def _extract_waterbody_name(text: str) -> str | None:
    """Regex-extract the most prominent waterbody name from free text. No API call."""
    m = _WATERBODY_RE.search(text)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = _LAKE_RE.search(text)
    if m:
        return f"Lake {m.group(1)}"
    return None


def _parse_centroid_wkt(wkt: str) -> tuple[float, float] | None:
    """Parse OHN POINT (lng lat) WKT. Returns (lat, lng) or None."""
    m = re.match(r"POINT\s*\(([^ ]+)\s+([^ )]+)\)", wkt)
    if m:
        return float(m.group(2)), float(m.group(1))  # lat, lng
    return None


def _has_landmark(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _LANDMARK_KEYWORDS)


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def _nominatim_geocode(query: str) -> tuple[float, float] | None:
    """Geocode via Nominatim. Appends ', Ontario, Canada'. Rate-limit: 1s sleep."""
    time.sleep(1)
    q = urllib.parse.quote(f"{query}, Ontario, Canada")
    url = f"https://nominatim.openstreetmap.org/search?q={q}&format=json&limit=3"
    headers = {"User-Agent": "FishBot/1.0 (personal fishing intelligence tool)"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            results = json.loads(r.read())
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        logger.warning("Nominatim geocode failed for %r: %s", query, e)
    return None


def _nearest_ohn_km(lat: float, lng: float) -> float:
    """Distance in km from (lat, lng) to the nearest OHN segment centroid."""
    tree, _ = _load_snap_data()
    if tree is None:
        return 0.0  # can't verify — assume within range
    dist_km, _ = tree.query([[lat * 111.0, lng * 80.5]])
    return float(dist_km[0])


def _query_segments_by_name(name: str, db: Any) -> list[dict]:
    """Return up to 20 named OHN segments matching name (case-insensitive LIKE)."""
    rows = db.execute(
        "SELECT ogf_id, name, geom_wkt FROM stream_segments "
        "WHERE LOWER(name) LIKE LOWER(?) ORDER BY LENGTH(name) LIMIT 20",
        [f"%{name}%"],
    ).fetchall()
    out = []
    for ogf_id, seg_name, wkt in rows:
        centroid = _parse_centroid_wkt(wkt or "")
        if centroid:
            out.append({"ogf_id": ogf_id, "name": seg_name, "lat": centroid[0], "lng": centroid[1]})
    return out


def _most_fished_candidate(candidates: list[dict], db: Any) -> dict | None:
    """Return the candidate segment most visited in parsed_trips, or None."""
    if "parsed_trips" not in db.table_names():
        return None
    ids = [c["ogf_id"] for c in candidates]
    if not ids:
        return None
    placeholders = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT ogf_id, COUNT(*) AS cnt FROM parsed_trips "
        f"WHERE ogf_id IN ({placeholders}) GROUP BY ogf_id ORDER BY cnt DESC LIMIT 1",
        ids,
    ).fetchall()
    if rows:
        best_id = rows[0][0]
        return next((c for c in candidates if c["ogf_id"] == best_id), None)
    return None


def _pick_closest_candidate(candidates: list[dict], lat: float, lng: float) -> dict:
    return min(candidates, key=lambda c: _haversine(lat, lng, c["lat"], c["lng"]))


def _result(
    lat: float | None,
    lng: float | None,
    method: str,
    confidence: float | None,
    ohn_segment_id: int | None = None,
    candidates: list[int] | None = None,
) -> dict:
    return {
        "lat": lat,
        "lng": lng,
        "method": method,
        "confidence": confidence,
        "ohn_segment_id": ohn_segment_id,
        "candidates": candidates or [],
    }


def resolve_location(location_text: str, db: Any) -> dict:
    """Resolve a free-text location to OHN segment coordinates.

    Layer 1 — named waterbody lookup in stream_segments.
    Layer 2 — Nominatim geocode + closest-candidate selection (landmark anchor).
    Layer 3 — Nominatim geocode on the full query text.
    Layer 4 — always returns text_only; never blocks.
    """
    # ── Layer 1: Named waterbody lookup ──────────────────────────────────────
    waterbody_name = _extract_waterbody_name(location_text)
    candidates: list[dict] = []

    if waterbody_name:
        candidates = _query_segments_by_name(waterbody_name, db)

        if len(candidates) == 1:
            c = candidates[0]
            return _result(c["lat"], c["lng"], "name_match", 0.85, ohn_segment_id=c["ogf_id"])

        if 2 <= len(candidates) <= 5:
            qualifier_words = [
                w
                for w in location_text.split()
                if len(w) > 4 and w.lower() not in waterbody_name.lower().split()
            ]
            if qualifier_words:
                seg_names = [c["name"] for c in candidates]
                best_names = difflib.get_close_matches(
                    " ".join(qualifier_words), seg_names, n=1, cutoff=0.4
                )
                if best_names:
                    matched = next(c for c in candidates if c["name"] == best_names[0])
                    return _result(
                        matched["lat"],
                        matched["lng"],
                        "name_match",
                        0.75,
                        ohn_segment_id=matched["ogf_id"],
                    )
            avg_lat = sum(c["lat"] for c in candidates) / len(candidates)
            avg_lng = sum(c["lng"] for c in candidates) / len(candidates)
            best = _pick_closest_candidate(candidates, avg_lat, avg_lng)
            return _result(
                avg_lat,
                avg_lng,
                "name_match",
                0.75,
                ohn_segment_id=best["ogf_id"],
                candidates=[c["ogf_id"] for c in candidates],
            )

        if len(candidates) >= 6:
            # ── Layer 2: Landmark anchor ──────────────────────────────────────
            geo = _nominatim_geocode(location_text)
            if geo:
                geo_lat, geo_lng = geo
                best = _pick_closest_candidate(candidates, geo_lat, geo_lng)
                dist = _haversine(geo_lat, geo_lng, best["lat"], best["lng"])
                if dist <= 15.0:
                    method = "landmark" if _has_landmark(location_text) else "name_match"
                    conf = 0.9 if method == "landmark" else 0.75
                    return _result(
                        best["lat"],
                        best["lng"],
                        method,
                        conf,
                        ohn_segment_id=best["ogf_id"],
                    )

            # Fall back: most-fished candidate (no blocking)
            mf = _most_fished_candidate(candidates, db)
            if mf:
                return _result(
                    mf["lat"],
                    mf["lng"],
                    "name_match",
                    0.4,
                    ohn_segment_id=mf["ogf_id"],
                )

    # ── Layer 3: Nominatim geocode on full text ───────────────────────────────
    geo = _nominatim_geocode(location_text)
    if geo:
        geo_lat, geo_lng = geo
        if _nearest_ohn_km(geo_lat, geo_lng) <= 2.0:
            return _result(geo_lat, geo_lng, "geocode_fallback", 0.6)

    # ── Layer 4: text_only — always returns, never blocks ─────────────────────
    return {
        "lat": None,
        "lng": None,
        "method": "text_only",
        "confidence": None,
        "ohn_segment_id": None,
        "candidates": [],
    }


# ── cKDTree snap ──────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _load_snap_data() -> tuple:
    """Lazy-load feature matrix and build KD tree. Cached after first call."""
    if not _FEATURE_MATRIX_PATH.exists():
        return None, None

    try:
        import pandas as pd
        from scipy.spatial import cKDTree

        df = pd.read_parquet(
            _FEATURE_MATRIX_PATH,
            columns=["ogf_id", "centroid_lat", "centroid_lng", "stream_order", "watercourse_name"],
        )
        df = df.dropna(subset=["centroid_lat", "centroid_lng"])
        coords = np.array(
            [[lat * 111.0, lng * 80.5] for lat, lng in zip(df["centroid_lat"], df["centroid_lng"])]
        )
        tree = cKDTree(coords)
        return tree, df.reset_index(drop=True)
    except Exception as e:
        logger.warning("Failed to load snap index: %s", e)
        return None, None


def snap_to_segment(lat: float, lng: float) -> dict:
    """Return the nearest OHN segment within _SNAP_RADIUS_KM, or {} if none found."""
    tree, df = _load_snap_data()
    if tree is None or df is None:
        return {}

    query = np.array([[lat * 111.0, lng * 80.5]])
    dist_km, idx = tree.query(query)
    dist_km = float(dist_km[0])
    idx = int(idx[0])

    if dist_km > _SNAP_RADIUS_KM:
        return {}

    row = df.iloc[idx]
    name = row["watercourse_name"]
    return {
        "ogf_id": int(row["ogf_id"]),
        "distance_to_segment_m": round(dist_km * 1000, 1),
        "segment_stream_order": (
            int(row["stream_order"]) if row["stream_order"] == row["stream_order"] else None
        ),
        "segment_watercourse_name": str(name) if name and str(name) != "nan" else None,
    }


# ── session extraction prompt ─────────────────────────────────────────────────

_SESSION_SYSTEM_PROMPT = """\
You are a fishing trip log parser. Extract structured data from the user's fishing
session description. A session is one day out fishing, which may include multiple stops
at different locations.

Return a JSON object with this exact structure:
{
  "date": "YYYY-MM-DD or null if unknown",
  "date_approx": "human readable date if exact is unknown, else null",
  "overall_notes": "anything that applies to the whole day, or null",
  "stops": [
    {
      "location_text": "exact location description as the user wrote it",
      "location_name": "cleaned display name if you can determine one, else null",
      "species_caught": [],
      "party_species_caught": [],
      "was_productive": true or false,
      "technique": "fishing technique used, or null",
      "gear": "bait/lure/tackle used, or null",
      "water_level": "low/normal/high/flooded or null",
      "water_clarity": "clear/slightly turbid/turbid or null",
      "weather_notes": "any weather or conditions notes, or null",
      "notes": "anything else specific to this stop, or null",
      "time_of_day": "dawn/morning/midday/afternoon/evening/night or null",
      "hour_of_day": "integer 0-23 or null (only if specific time mentioned)"
    }
  ]
}

STOP SPLITTING RULES:
- Create a separate stop for each distinct location the user visited
- If the user went somewhere and left because conditions were bad, that is still a stop
  (was_productive: false, notes: reason they left)
- If the user mentions multiple spots on the same water body but clearly moved around,
  use your judgment — err toward separate stops

SPECIES EXTRACTION — STRICT RULES (these override everything):
1. Only extract species the user explicitly named
2. Vague group names → "unidentified [group] sp." e.g. "unidentified shiner sp."
3. Uncertain IDs → "species name (uncertain)"
4. Never add species from your knowledge of what lives in that area
5. Never add species the user only observed but did not catch
6. Caught nothing → empty list []
7. Do not guess, infer, or helpfully fill in any species

PARTY vs. USER DISTINCTION — CRITICAL:
- species_caught: ONLY what the first-person narrator ("I", "me", "my rod")
  explicitly caught. If the user says "my friend caught a drum", that drum goes
  in party_species_caught only, NOT in species_caught.
- party_species_caught: Everything caught by anyone in the group, including the
  user. This is the superset. If only the user caught fish, party_species_caught
  equals species_caught.
- was_productive: true only if the USER personally caught fish, not just the party.
  "My friend caught a 10lb cat but I got nothing" = was_productive: false.

Examples:
  "I caught a channel cat, my friend caught a drum" →
    species_caught: ["channel catfish"]
    party_species_caught: ["channel catfish", "freshwater drum"]

  "Between us we caught 3 redhorse" (unclear who caught what) →
    species_caught: ["shorthead redhorse (uncertain)"]
    party_species_caught: ["shorthead redhorse"]

  "My friend got a bowfin, I blanked" →
    species_caught: []
    party_species_caught: ["bowfin"]
    was_productive: false

LOCATION RULES:
- location_text must always be populated — copy the user's words exactly
- location_name is your best attempt at a clean display name
- Do not add lat/lng — the system resolves coordinates separately

PRODUCTIVITY RULES:
- was_productive is about THIS STOP specifically, not the overall day
- A stop where the user caught nothing is was_productive: false
- A stop where a friend caught something but the user caught nothing:
  was_productive: false (log from the user's perspective)
- A stop cut short due to conditions: was_productive: false

TIME OF DAY:
- Extract time_of_day from mentions like "dawn session", "fished until dark",
  "noon", "early morning", "sunset", "8pm", "this morning", etc.
- hour_of_day: only set if a specific time is mentioned ("caught at 10am" → 10,
  "8pm" → 20). Use 24-hour integers.
- If no time mentioned, both are null — do not guess\
"""


# ── main entry points ─────────────────────────────────────────────────────────


def parse_session_from_text(text: str, db_conn: Any, *, user_id: int | None) -> dict:
    """Parse a natural-language fishing session description into structured data.

    Calls Claude to extract a session with one or more stops, validates species
    for each stop, and resolves location for each stop independently.
    Never blocks — unresolvable locations use method="text_only".
    """
    response = llm.complete(
        "trip_parse",
        caller="trip_parser.parse_session_from_text",
        user_id=user_id,
        max_tokens=2048,
        system=_SESSION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

    parsed: dict = json.loads(raw)

    known_species = _build_known_species(db_conn)
    for stop in parsed.get("stops", []):
        stop["species_caught"] = _validate_species(
            stop.get("species_caught") or [], text, known_species
        )
        stop["party_species_caught"] = _validate_species(
            stop.get("party_species_caught") or [], text, known_species
        )
        loc = resolve_location(stop.get("location_text") or text, db_conn)
        stop["lat"] = loc.get("lat")
        stop["lng"] = loc.get("lng")
        stop["ohn_segment_id"] = str(loc["ohn_segment_id"]) if loc.get("ohn_segment_id") else None
        stop["location_method"] = loc.get("method", "text_only")
        stop["location_confidence"] = loc.get("confidence")

    return parsed


def parse_trip_from_text(
    text: str,
    user_lat: float | None = None,
    user_lng: float | None = None,
    db: Any = None,
    *,
    user_id: int | None = 1,
) -> dict:
    """DEPRECATED — use parse_session_from_text instead.

    Calls Claude to extract structured trip data, resolves location, snaps to OHN segment.
    """
    response = llm.complete(
        "trip_parse",
        caller="trip_parser.parse_trip_from_text",
        user_id=user_id,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _USER_PROMPT_TEMPLATE.format(text=text)}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

    parsed: dict = json.loads(raw)
    known_species = _build_known_species(db) if db is not None else None
    parsed["species_caught"] = _validate_species(
        parsed.get("species_caught") or [], text, known_species
    )

    lat = parsed.get("lat") or user_lat
    lng = parsed.get("lng") or user_lng
    loc_meta: dict = {}

    if (lat is None or lng is None) and db is not None:
        location_text = parsed.get("waterbody_name") or parsed.get("location_description") or text
        loc = resolve_location(location_text, db)
        if loc["lat"] is not None:
            lat = loc["lat"]
            lng = loc["lng"]
            loc_meta = {
                "location_method": loc["method"],
                "location_confidence": loc["confidence"],
            }

    result = dict(parsed)
    if lat is not None and lng is not None:
        result["lat"] = lat
        result["lng"] = lng
        snap = snap_to_segment(float(lat), float(lng))
        result.update(snap)

    result.update(loc_meta)
    return result
