"""High-level trip logging service.

Orchestrates: NL parsing → segment snapping → DB insert → insight generation.
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime

from sqlite_utils.db import Database

from src.services.trip_parser import parse_trip_from_text
from src.storage.catches import insert_catch, update_personal_best_if_higher
from src.storage.trips import get_parsed_trips, insert_parsed_trip

logger = logging.getLogger(__name__)


def _photo_species_candidates(db_conn: Database, photo_path: str) -> dict | None:
    """Run photo-based species suggestion for a stop's photo. Never raises —
    a vision failure just means the catch falls back to text-only suggestion,
    it never blocks logging."""
    try:
        from PIL import Image

        from src.services.species_vision import (
            annotate_conservation,
            get_region_candidate_species,
            suggest_species_from_photo,
        )

        candidates = get_region_candidate_species(db_conn)
        if not candidates:
            return None

        import io

        img = Image.open(photo_path).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        suggestion = suggest_species_from_photo(buf.getvalue(), candidates, media_type="image/jpeg")
        # After the identification, never before it — a conservation status in
        # the prompt would bias what the model reports seeing.
        return annotate_conservation(db_conn, suggestion)
    except Exception as e:
        logger.warning("Photo species suggestion failed for %s: %s", photo_path, e)
        return None


_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def _build_suggested_species(text_species: str, vision_result: dict | None) -> list[dict]:
    """Combine the text-parsed species with any photo-vision candidates into
    one ranked suggestion list, for the confirm UI.

    A vague "unidentified [group] sp." text entry is not a competing species
    guess — it's an honest admission the user didn't name anything specific.
    It's never given a confidence tier and never ranked above a real photo
    ID; it's appended at the end with a note explaining the gap instead.
    A specific text-named species (flagged low-confidence if the parser
    marked it (uncertain)/(unresolved)) IS a real candidate and is ranked by
    confidence alongside photo candidates, deduped against a matching photo
    candidate rather than listed twice.
    """
    text_clean = text_species
    is_unspecified = text_clean.lower().startswith("unidentified")
    text_confidence = "high"
    for tag in ("(unresolved)", "(uncertain)"):
        if tag in text_clean.lower():
            text_confidence = "low"
            text_clean = text_clean.lower().replace(tag, "").strip()

    ranked: list[dict] = []
    if vision_result and vision_result.get("candidates"):
        for c in vision_result["candidates"]:
            name = (c.get("species") or "").strip()
            if name:
                ranked.append(
                    {"species": name, "source": "photo", "confidence": c.get("confidence", "low")}
                )

    if not is_unspecified:
        already_covered = any(r["species"].lower() == text_clean.lower() for r in ranked)
        if not already_covered:
            ranked.append({"species": text_clean, "source": "text", "confidence": text_confidence})

    ranked.sort(key=lambda r: _CONFIDENCE_RANK.get(r["confidence"], 0), reverse=True)

    if is_unspecified:
        ranked.append(
            {
                "species": text_clean,
                "source": "text",
                "confidence": None,
                "note": "not specified in your notes",
            }
        )

    return ranked


_CONFIDENCE_TAG_RE = re.compile(r"\s*\((?:uncertain|unresolved)\)\s*$", re.IGNORECASE)


def _strip_confidence_tags(species: str) -> str:
    """Drop a trailing "(uncertain)"/"(unresolved)" tag so a structured
    catch's typed species (plain text, no tag) can be matched against the
    NL parser's flagged output for the same fish."""
    return _CONFIDENCE_TAG_RE.sub("", species).strip()


_SIZE_RE = re.compile(
    r"([-+]?[0-9]*\.?[0-9]+)\s*(cm|centimeters?|in|inch(?:es)?|\")?", re.IGNORECASE
)


def parse_size_to_cm(raw: str | float | int | None) -> float | None:
    """Normalize a user-entered fish size to centimeters.

    Accepts a bare number (the catches_json contract's `biggest_size_cm`
    field is documented as already-converted centimeters) or a string with
    an embedded unit suffix — 'cm'/'in'/'inch(es)'/'"' — which is what the
    LogTrip UI's `biggest_size` field actually sends today (e.g. "14in",
    "35.5cm"). No unit suffix defaults to centimeters, matching the
    documented contract. Unparseable/empty input returns None rather than
    guessing.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    m = _SIZE_RE.match(text)
    if not m:
        return None
    value = float(m.group(1))
    unit = (m.group(2) or "").strip().lower()
    if unit in ("in", "inch", "inches", '"'):
        return value * 2.54
    return value


def _maybe_update_personal_best(
    db_conn: Database, user_id: int, species: str | None, size_cm: float | None, catch_id: int
) -> bool:
    """Update the stored personal-best size for this species if this catch
    beats it. Never raises — bookkeeping failure must not block logging,
    same policy as _photo_species_candidates. Returns whether this catch
    became the new personal best, so callers (the summary card's PB flag)
    can surface it without a second lookup."""
    if not species or size_cm is None:
        return False
    try:
        return update_personal_best_if_higher(
            db_conn, user_id=user_id, species=species, size_cm=size_cm, catch_id=catch_id
        )
    except Exception as e:
        logger.warning("Personal-best update failed for %s: %s", species, e)
        return False


def log_session(
    parsed_session: dict,
    db_conn: Database,
    user_id: int = 1,
    structured_catches: list[dict] | None = None,
    fallback_lat: float | None = None,
    fallback_lng: float | None = None,
    fallback_photo_taken_at: str | None = None,
    fallback_photo_url: str | None = None,
    fallback_photo_path: str | None = None,
) -> dict:
    """Insert a parsed session and all its stops into the database.

    structured_catches (the multi-catch logging UI's catches_json, already
    normalized by src/api/main.py's _normalize_structured_catches) carries
    count/biggest_size_cm/bait/photo per catch that the NL parser never
    extracts from free text. Each entry is matched by name against the first
    stop's NL-parsed species_caught (one NL match consumed per structured
    entry, so two structured catches of the same species — e.g. two separate
    smallmouth bass logged with different sizes — don't collide): a match
    merges structured count/size/bait/photo onto that NL-derived row (species
    confidence/confirmation still comes from the NL+vision suggestion flow);
    an unmatched structured entry becomes its own row instead. Any NL species
    a structured entry didn't cover still logs the old way. Absent/empty
    structured_catches is a complete no-op — pure NL-parsed behavior,
    unchanged, exercised by every caller that doesn't pass it.

    fallback_lat/fallback_lng (plus the photo metadata that rides with them —
    e.g. the "Start fishing" device geolocation, or a catch photo's EXIF GPS)
    exist to cover a gap in parse_session_from_text: it's an LLM call, and
    generic text with no location/narrative content (composeSessionText's
    "Fishing trip." fallback, sent when a session is nothing but untagged
    fast-tally taps and blank session notes) legitimately parses to zero
    stops. Without this, log_session's per-stop loop below never runs and
    every real structured catch — every "+1 fish" tap, any detailed card —
    is silently discarded even though the API reports success. The NL
    parser's job is enrichment (location name, technique, conditions), not
    gatekeeping whether real structured data gets saved — so if there are
    structured catches to persist and parse_session_from_text found no stop
    to hold them, synthesize one from whatever location data the client
    already captured. See test_fast_tally_session_with_no_notes_still_persists.

    Returns {"session_id": int, "stops_logged": int, ...}
    """
    if not parsed_session.get("stops") and structured_catches:
        parsed_session["stops"] = [
            {
                "location_text": "",
                "location_name": None,
                "species_caught": [],
                "party_species_caught": [],
                "was_productive": True,
                "lat": fallback_lat,
                "lng": fallback_lng,
                "location_method": "device_geolocation" if fallback_lat is not None else "unknown",
                "location_confidence": 0.95 if fallback_lat is not None else None,
                "photo_lat": fallback_lat,
                "photo_lng": fallback_lng,
                "photo_taken_at": fallback_photo_taken_at,
                "photo_url": fallback_photo_url,
                "photo_path": fallback_photo_path,
            }
        ]

    session_id = (
        db_conn["sessions"]
        .insert(
            {
                "date": parsed_session.get("date"),
                "date_approx": parsed_session.get("date_approx"),
                "overall_notes": parsed_session.get("overall_notes"),
                "user_id": user_id,
            }
        )
        .last_pk
    )

    stops_logged = 0
    pending_catches: list[dict] = []
    # Every catch inserted this call, confirmed or not — the summary card
    # needs this beyond just pending_catches (which only covers unconfirmed
    # rows) because a directly-typed species or a fast-tally tap is already
    # confirmed at insert time and would otherwise never appear anywhere in
    # this call's response.
    catches_summary: list[dict] = []
    for stop_index, stop in enumerate(parsed_session.get("stops", [])):
        stop_id = (
            db_conn["stops"]
            .insert(
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "location_text": stop.get("location_text") or "",
                    "location_name": stop.get("location_name"),
                    "lat": stop.get("lat"),
                    "lng": stop.get("lng"),
                    "ohn_segment_id": stop.get("ohn_segment_id"),
                    "location_method": stop.get("location_method", "text_only"),
                    "location_confidence": stop.get("location_confidence"),
                    "species_caught": json.dumps(stop.get("species_caught") or []),
                    "party_species_caught": json.dumps(stop.get("party_species_caught") or []),
                    "was_productive": 1 if stop.get("was_productive") else 0,
                    "technique": stop.get("technique"),
                    "gear": stop.get("gear"),
                    "water_level": stop.get("water_level"),
                    "water_clarity": stop.get("water_clarity"),
                    "water_temp_c": stop.get("water_temp_c"),
                    "weather_notes": stop.get("weather_notes"),
                    "notes": stop.get("notes"),
                    "time_of_day": stop.get("time_of_day"),
                    "hour_of_day": stop.get("hour_of_day"),
                    "photo_lat": stop.get("photo_lat"),
                    "photo_lng": stop.get("photo_lng"),
                    "photo_taken_at": stop.get("photo_taken_at"),
                    "photo_url": stop.get("photo_url"),
                }
            )
            .last_pk
        )
        stops_logged += 1

        # One catches row per species caught at this stop — gives each species its
        # own record to eventually carry its own count/size/bait/photo, while
        # stops.species_caught above stays intact for existing readers.
        #
        # species_confirmed=False on every NL-derived row here: the species came
        # from the NL parser (and, if there's a photo, a Claude vision suggestion
        # too) — both fallible AI suggestions, never committed as fact until the
        # user confirms via POST /catches/{id}/confirm-species.
        photo_path = stop.get("photo_path")
        vision_result = _photo_species_candidates(db_conn, photo_path) if photo_path else None

        # structured_catches only ever applies to the first stop — the
        # multi-catch UI captures one flat session, not per-stop structure,
        # same convention _log_trip_core already uses for photo metadata.
        stop_structured = structured_catches if stop_index == 0 and structured_catches else []
        remaining_nl_species = list(stop.get("species_caught") or [])

        # Pass 1 — every structured catches_json entry becomes exactly one
        # row. Where it names a species the NL parser also found, that NL
        # entry is consumed (popped) so a second structured entry with the
        # same species name doesn't double-match it; where it doesn't match
        # anything (or names no species at all), it's inserted independently.
        for sc in stop_structured:
            sc_species_key = (sc.get("species") or "").strip().lower()
            matched_nl_species = None
            if sc_species_key:
                for i, nl_species in enumerate(remaining_nl_species):
                    if _strip_confidence_tags(nl_species).lower() == sc_species_key:
                        matched_nl_species = remaining_nl_species.pop(i)
                        break

            sc_photo_path = sc.get("photo_path")
            if sc_photo_path:
                # This catch has its own photo from the multi-catch UI —
                # vision runs against that photo specifically rather than
                # the stop's (possibly unrelated) shared photo.
                catch_photo_path = sc_photo_path
                catch_photo_url = sc.get("photo_url")
                catch_vision = _photo_species_candidates(db_conn, sc_photo_path)
            elif matched_nl_species or (
                not sc.get("species")
                and stop.get("photo_path")
                and sc.get("source") != "fast_tally"
            ):
                # Either an NL match, or (the "let vision suggest it" card)
                # no typed species at all — either way this catch has no
                # photo of its own, so it rides on the stop's shared photo
                # instead of falling through to the no-photo/no-species
                # branch below and losing count/size/bait. Fast-tally taps
                # are excluded here even when untagged: they're anonymous by
                # design, and a session can contain several of them
                # alongside one unrelated detailed photo card — letting all
                # of them ride that one photo would mis-attach a vision
                # guess to fish that were never in that photo. See
                # test_fast_tally_does_not_ride_unrelated_stop_photo.
                catch_photo_path = stop.get("photo_path")
                catch_photo_url = stop.get("photo_url")
                catch_vision = vision_result
            else:
                catch_photo_path = None
                catch_photo_url = None
                catch_vision = None

            if matched_nl_species:
                suggestions = _build_suggested_species(matched_nl_species, catch_vision)
                final_species = matched_nl_species
                confirmed = False
            elif catch_photo_path:
                # No NL match but a photo exists — species (typed or not) is
                # still a suggestion until reconciled against vision, same
                # confirm-species flow as the NL path.
                fallback_species = sc.get("species") or "unidentified sp."
                suggestions = _build_suggested_species(fallback_species, catch_vision)
                final_species = sc.get("species") or (
                    suggestions[0]["species"] if suggestions else "unidentified sp."
                )
                confirmed = False
            elif sc.get("species"):
                # Typed directly, no photo, no NL match — trusted human
                # input, not an AI guess, so it skips the confirm step (see
                # insert_catch's species_confirmed default).
                suggestions = None
                final_species = sc["species"]
                confirmed = True
            elif sc.get("source") == "fast_tally":
                # Fast-tally: user explicitly declined to name it — there's
                # no AI guess to confirm here (no photo, no NL match), so
                # this is trusted-but-unidentified, not a pending
                # suggestion. Reuses the same "unidentified sp." placeholder
                # string as the photo-only fallback above so both paths
                # group under one FishDex bucket instead of fragmenting
                # into two unmapped species buckets.
                suggestions = None
                final_species = "unidentified sp."
                confirmed = True
            else:
                # No species, no photo, no NL match, not a fast-tally
                # entry — nothing to identify this catch by. Skip rather
                # than invent a row. Unchanged from before this feature:
                # today the frontend's payload filter already excludes this
                # case for detailed cards, so this branch was effectively
                # dead code until fast-tally introduced a legitimate reason
                # to reach it.
                continue

            size_cm = sc.get("biggest_size_cm")
            catch_id = insert_catch(
                db_conn,
                stop_id=stop_id,
                session_id=session_id,
                user_id=user_id,
                species=final_species,
                count=sc.get("count"),
                biggest_size_cm=size_cm,
                bait=sc.get("bait"),
                photo_path=catch_photo_path,
                photo_url=catch_photo_url,
                photo_lat=stop.get("photo_lat") if catch_photo_path else None,
                photo_lng=stop.get("photo_lng") if catch_photo_path else None,
                photo_taken_at=stop.get("photo_taken_at") if catch_photo_path else None,
                caught_at=sc.get("caught_at"),
                species_confirmed=confirmed,
                suggested_species=suggestions,
            )
            if not confirmed:
                pending_catches.append(
                    {
                        "catch_id": catch_id,
                        "suggested_species": suggestions,
                        "photo_url": catch_photo_url,
                    }
                )
            is_new_pb = _maybe_update_personal_best(
                db_conn, user_id, final_species, size_cm, catch_id
            )
            catches_summary.append(
                {
                    "catch_id": catch_id,
                    "species": final_species,
                    "species_confirmed": confirmed,
                    "count": sc.get("count"),
                    "biggest_size_cm": size_cm,
                    "photo_url": catch_photo_url,
                    "is_new_pb": is_new_pb,
                }
            )

        # Pass 2 — any NL-parsed species no structured entry covered. This is
        # the *only* pass that runs when structured_catches is empty/absent,
        # identical to the pre-multi-catch behavior (no regression).
        for species in remaining_nl_species:
            if not species:
                continue
            suggestions = _build_suggested_species(species, vision_result)
            catch_id = insert_catch(
                db_conn,
                stop_id=stop_id,
                session_id=session_id,
                user_id=user_id,
                species=species,
                photo_path=stop.get("photo_path"),
                photo_url=stop.get("photo_url"),
                photo_lat=stop.get("photo_lat"),
                photo_lng=stop.get("photo_lng"),
                photo_taken_at=stop.get("photo_taken_at"),
                species_confirmed=False,
                suggested_species=suggestions,
            )
            pending_catches.append(
                {
                    "catch_id": catch_id,
                    "suggested_species": suggestions,
                    "photo_url": stop.get("photo_url"),
                }
            )
            catches_summary.append(
                {
                    "catch_id": catch_id,
                    "species": species,
                    "species_confirmed": False,
                    "count": None,
                    "biggest_size_cm": None,
                    "photo_url": stop.get("photo_url"),
                    "is_new_pb": False,  # NL-only rows never carry a size, so never a PB
                }
            )

        if not stop.get("was_productive") and stop.get("ohn_segment_id"):
            try:
                _penalise_segment(db_conn, int(stop["ohn_segment_id"]), user_id)
            except (ValueError, TypeError):
                pass

    followup_questions = []
    proactive_coaching = None
    try:
        from src.agent.client import get_client
        from src.services.trip_enrichment import enrich_session

        client = get_client()
        enrichment = enrich_session(db_conn, session_id, client, user_id=user_id)
        followup_questions = enrichment.get("followup_questions", [])
        proactive_coaching = enrichment.get("proactive_coaching")
    except Exception as e:
        print(f"[ENRICHMENT] Non-fatal error: {e}")

    # Determine session coordinates and datetime for condition enrichment
    enrich_lat = None
    enrich_lng = None
    enrich_dt = None
    session_date = parsed_session.get("date")
    photo_taken_at = None
    for stop in parsed_session.get("stops", []):
        if stop.get("photo_taken_at"):
            photo_taken_at = stop["photo_taken_at"]
        if stop.get("photo_lat") and enrich_lat is None:
            enrich_lat = stop["photo_lat"]
            enrich_lng = stop["photo_lng"]
        if stop.get("lat") and enrich_lat is None:
            enrich_lat = stop["lat"]
            enrich_lng = stop["lng"]

    if photo_taken_at:
        try:
            enrich_dt = datetime.fromisoformat(photo_taken_at.replace("Z", "+00:00"))
        except Exception:
            pass
    elif session_date:
        try:
            enrich_dt = datetime.fromisoformat(f"{session_date}T12:00:00")
        except Exception:
            pass

    conditions_result = None
    if enrich_lat is not None and enrich_dt is not None:
        try:
            import concurrent.futures

            from src.services.trip_enrichment_conditions import enrich_session_conditions

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    enrich_session_conditions,
                    db_conn,
                    session_id,
                    enrich_lat,
                    enrich_lng,
                    enrich_dt,
                    timeout_seconds=3.0,
                )
                try:
                    conditions_result = future.result(timeout=3.5)
                except concurrent.futures.TimeoutError:
                    conditions_result = {"timeout": True, "queued": True}
        except Exception as e:
            conditions_result = {"error": str(e)}

    # The derived layer is recomputed here, on the write, rather than on every
    # question that needs it. Its inputs just changed and nothing else will
    # tell it so. Failure is non-fatal — the read path detects a stale
    # fingerprint and rebuilds — but it is logged, because a recompute that
    # never succeeds looks exactly like one that was never needed.
    patterns_recomputed = False
    try:
        from src.services.context import recompute_user_layer

        recompute_user_layer(db_conn, user_id=user_id)
        patterns_recomputed = True
    except Exception:  # noqa: BLE001 - a logged trip must never fail over this
        logger.warning(
            "Could not recompute the derived layer for user %s after logging "
            "session %s; it will be rebuilt on next read",
            user_id,
            session_id,
            exc_info=True,
        )

    return {
        "session_id": session_id,
        "stops_logged": stops_logged,
        "pending_catches": pending_catches,
        "catches": catches_summary,
        "followup_questions": followup_questions,
        "proactive_coaching": proactive_coaching,
        "patterns_recomputed": patterns_recomputed,
        "conditions_enriched": conditions_result is not None
        and not conditions_result.get("timeout"),
        "conditions": conditions_result,
    }


def log_trip(  # DEPRECATED — use log_session / parse_session_from_text instead
    db: Database,
    text: str,
    user_lat: float | None = None,
    user_lng: float | None = None,
    user_id: int = 1,
) -> dict:
    """Parse a natural-language trip description and persist it.

    Returns a summary dict with trip_id, parsed data, snap info, and a
    human-readable confirmation string.
    """
    parsed = parse_trip_from_text(text, user_lat=user_lat, user_lng=user_lng, db=db)

    if parsed.get("status") == "needs_location":
        return {
            "trip_id": None,
            "parsed": {},
            "segment_snapped": False,
            "segment_name": "",
            "insights_generated": 0,
            "confirmation": parsed["message"],
        }

    parsed["raw_text"] = text
    trip_id = insert_parsed_trip(db, parsed)

    ogf_id = parsed.get("ogf_id")
    seg_name = parsed.get("segment_watercourse_name")
    snapped = ogf_id is not None
    insights_generated = 0

    # Insert habitat/presence insights for caught species at a known segment
    if snapped and parsed.get("species_caught"):
        insights_generated = _maybe_insert_species_insights(db, parsed, ogf_id, seg_name)

    # Mark unproductive segments as seen-before (0.3× exploration penalty)
    if parsed.get("was_productive") is False and ogf_id:
        _penalise_segment(db, ogf_id, user_id)

    confirmation = _build_confirmation(parsed, trip_id, snapped, seg_name)

    return {
        "trip_id": trip_id,
        "parsed": parsed,
        "segment_snapped": snapped,
        "segment_name": seg_name or "",
        "insights_generated": insights_generated,
        "confirmation": confirmation,
    }


def get_trip_summary(db: Database, user_id: int = 1) -> str:
    """Return a natural-language summary of all logged trips for agent context."""
    has_stops = "stops" in db.table_names()
    has_parsed = "parsed_trips" in db.table_names()

    if not has_stops and not has_parsed:
        return "No trips logged yet."

    caught_counter: Counter = Counter()
    location_counter: Counter = Counter()
    dates: list[str] = []
    n_stops = 0
    n_sessions = 0

    if has_stops:
        stops = list(db["stops"].rows_where("user_id = ?", [user_id]))
        n_stops = len(stops)
        n_sessions = db.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id = ?", [user_id]
        ).fetchone()[0]
        for stop in stops:
            species = json.loads(stop.get("species_caught") or "[]")
            for sp in species:
                if sp:
                    caught_counter[sp] += 1
            loc = stop.get("location_name") or stop.get("location_text") or ""
            if loc:
                location_counter[loc[:50]] += 1

        session_dates = list(
            db.execute(
                "SELECT date FROM sessions WHERE date IS NOT NULL AND user_id = ?",
                [user_id],
            ).fetchall()
        )
        dates = [r[0] for r in session_dates]

    elif has_parsed:
        trips = get_parsed_trips(db, limit=500)
        n_stops = len(trips)
        n_sessions = n_stops
        for t in trips:
            for sp in t.get("species_caught") or []:
                if sp:
                    caught_counter[sp] += 1
            loc = t.get("waterbody_name") or t.get("location_description", "")
            if loc:
                location_counter[loc[:50]] += 1
            d = t.get("trip_date") or t.get("logged_at", "")[:10]
            if d:
                dates.append(d)

    if n_stops == 0 and n_sessions == 0:
        return "No trips logged yet."

    parts = [
        f"You have logged {n_sessions} session{'s' if n_sessions != 1 else ''} "
        f"({n_stops} stop{'s' if n_stops != 1 else ''})."
    ]

    if caught_counter:
        top = caught_counter.most_common(3)
        sp_str = ", ".join(f"{sp} ({c}×)" for sp, c in top)
        parts.append(f"Most-caught: {sp_str}.")
    else:
        parts.append("No fish caught yet in the log.")

    if location_counter:
        top_loc = location_counter.most_common(1)[0][0]
        parts.append(f"Most-visited: {top_loc}.")

    if dates:
        parts.append(f"Last logged: {max(dates)}.")

    return " ".join(parts)


def get_trips_at_location(
    db: Database,
    location_query: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 2.0,
    user_id: int = 1,
) -> str:
    """Return a summary of the user's logged stops at a specific location.

    Matches by location name (fuzzy) OR by proximity if lat/lng given.
    """
    import math

    if "stops" not in db.table_names():
        return "No trips logged yet."

    stops = list(db["stops"].rows_where("user_id = ?", [user_id]))
    if not stops:
        return "No trips logged yet."

    def haversine(la1, ln1, la2, ln2):
        R = 6371
        dlat = math.radians(la2 - la1)
        dlng = math.radians(ln2 - ln1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(la1)) * math.cos(math.radians(la2)) * math.sin(dlng / 2) ** 2
        )
        return R * 2 * math.asin(math.sqrt(a))

    matched = []
    for stop in stops:
        loc = stop.get("location_name") or stop.get("location_text") or ""

        name_match = bool(location_query and location_query.lower() in loc.lower())

        prox_match = False
        if lat is not None and lng is not None and stop.get("lat") and stop.get("lng"):
            if haversine(lat, lng, stop["lat"], stop["lng"]) <= radius_km:
                prox_match = True

        if name_match or prox_match:
            matched.append(stop)

    if not matched:
        target = location_query or f"{lat},{lng}"
        return f"No logged trips found at {target}."

    lines = []
    for stop in matched:
        try:
            session = db["sessions"].get(stop["session_id"]) if stop.get("session_id") else None
        except Exception:
            session = None
        date = (session.get("date") or session.get("date_approx")) if session else None
        user_species = json.loads(stop.get("species_caught") or "[]")
        party_species = json.loads(stop.get("party_species_caught") or "[]")

        date_str = date or "undated"
        if user_species:
            catch_str = f"you caught: {', '.join(user_species)}"
            party_only = [s for s in party_species if s not in user_species]
            if party_only:
                catch_str += f" (others in party: {', '.join(party_only)})"
        elif party_species:
            catch_str = f"blanked personally (party caught: {', '.join(party_species)})"
        else:
            catch_str = "no fish (blank)"

        detail_bits = []
        if stop.get("technique"):
            detail_bits.append(stop["technique"])
        if stop.get("gear"):
            detail_bits.append(stop["gear"])
        if stop.get("water_level"):
            detail_bits.append(f"{stop['water_level']} water")
        detail = f" — {'; '.join(detail_bits)}" if detail_bits else ""

        lines.append(f"{date_str}: {catch_str}{detail}")

    location_label = (
        matched[0].get("location_name") or matched[0].get("location_text") or location_query
    )
    visit_count = len(matched)
    plural = "s" if visit_count != 1 else ""
    header = f"Your logged trips at {location_label} ({visit_count} visit{plural}):"
    return header + "\n" + "\n".join(f"- {line}" for line in lines)


# ── helpers ───────────────────────────────────────────────────────────────────


def _maybe_insert_species_insights(
    db: Database,
    parsed: dict,
    ogf_id: int,
    seg_name: str | None,
) -> int:
    """Generate one presence insight per caught species at the snapped segment."""
    from src.models.behavioral_insight import BehavioralInsight
    from src.storage.insights import insert_insight

    inserted = 0
    trip_date = parsed.get("date") or datetime.now().strftime("%Y-%m-%d")
    loc = seg_name or parsed.get("location_description", f"segment {ogf_id}")
    stream_order = parsed.get("segment_stream_order")
    order_note = f" (order-{stream_order} stream)" if stream_order else ""

    for species in parsed.get("species_caught") or []:
        if not species:
            continue
        habitat_notes = parsed.get("habitat_notes") or ""
        spot_type = parsed.get("spot_type") or ""
        context_parts = [p for p in [spot_type, habitat_notes] if p]
        condition_context = ", ".join(context_parts) if context_parts else "personal_trip_log"

        conclusion = (
            f"{species} confirmed caught at {loc}{order_note} on {trip_date} "
            f"(personal trip log, ogf_id={ogf_id})"
        )
        if parsed.get("water_clarity"):
            conclusion += f". Water clarity: {parsed['water_clarity']}"
        if parsed.get("water_level"):
            conclusion += f", level: {parsed['water_level']}"

        insight = BehavioralInsight(
            species=species,
            condition_type="habitat",
            condition_context=condition_context,
            conclusion=conclusion,
            confidence="high",
            source_type="trip_log",
            source_detail=f"personal trip log: {loc}, {trip_date}",
            evidence_count=1,
            jurisdiction="CA-ON",
        )
        try:
            insert_insight(db, insight)
            inserted += 1
        except Exception as e:
            logger.warning("Failed to insert insight for %s: %s", species, e)

    return inserted


def _penalise_segment(db: Database, ogf_id: int, user_id: int) -> None:
    """Add an unproductive segment to this user's dismissed_segments (0.3× exploration penalty)."""
    try:
        db["dismissed_segments"].upsert(
            {
                "user_id": user_id,
                "ogf_id": ogf_id,
                "dismissed_at": datetime.now().isoformat(),
                "reason": "unproductive_trip_log",
            },
            pk=("user_id", "ogf_id"),
        )
    except Exception as e:
        logger.warning("Failed to penalise segment %s: %s", ogf_id, e)


def _build_confirmation(
    parsed: dict,
    trip_id: int,
    snapped: bool,
    seg_name: str | None,
) -> str:
    lines = [f"Trip #{trip_id} logged."]

    date_str = parsed.get("date") or "date unknown"
    loc = parsed.get("location_description") or "location unknown"
    lines.append(f"  Date: {date_str} — {loc}")

    caught = parsed.get("species_caught") or []
    observed = parsed.get("species_observed") or []
    if caught:
        lines.append(f"  Caught: {', '.join(caught)}")
    if observed and observed != caught:
        lines.append(f"  Observed: {', '.join(observed)}")
    if not caught and not observed:
        lines.append("  No species recorded.")

    cond_parts = []
    conds = parsed.get("conditions") or {}
    if conds.get("water_level"):
        cond_parts.append(f"water {conds['water_level']}")
    if conds.get("water_clarity"):
        cond_parts.append(conds["water_clarity"])
    if conds.get("flow_trend"):
        cond_parts.append(f"flow {conds['flow_trend']}")
    if cond_parts:
        lines.append(f"  Conditions: {', '.join(cond_parts)}")

    if snapped:
        dist = parsed.get("distance_to_segment_m")
        name = seg_name or f"segment {parsed.get('ogf_id')}"
        dist_str = f" ({int(dist)}m away)" if dist else ""
        lines.append(f"  Snapped to: {name}{dist_str}")
    else:
        lines.append("  No OHN segment found within 5km.")

    return "\n".join(lines)
