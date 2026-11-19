"""
Retroactively enrich historical sessions that have coordinates but no
conditions data. Fetches weather from Open-Meteo archive API and water
quality from local PWQMN data.

Usage: uv run python scripts/historical/enrich_historical_sessions.py [--dry-run]
"""
import sys
import argparse
from datetime import datetime

sys.path.insert(0, "src")
from storage.database import get_db, ensure_schema
from services.trip_enrichment_conditions import enrich_session_conditions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be enriched without doing it")
    args = parser.parse_args()

    db = get_db()
    ensure_schema(db)

    # Auto-discover sessions with coordinates but no conditions
    rows = list(db.execute("""
        SELECT DISTINCT s.id, s.date, s.date_approx,
               st.lat, st.lng, st.photo_lat, st.photo_lng,
               st.photo_taken_at, st.location_name, st.location_text
        FROM sessions s
        JOIN stops st ON st.session_id = s.id
        LEFT JOIN session_conditions sc ON sc.session_id = s.id
        WHERE sc.session_id IS NULL
          AND (st.lat IS NOT NULL OR st.photo_lat IS NOT NULL)
        ORDER BY s.id
    """).fetchall())

    if not rows:
        print("All sessions with coordinates are already enriched.")
        return

    print(f"\nFound {len(rows)} sessions to enrich:\n")
    print(f"{'ID':<5} {'Date':<12} {'Location':<35} {'Lat':<10} {'Lng':<11}")
    print("-" * 75)

    for r in rows:
        sid, date_str, date_approx, lat, lng, photo_lat, photo_lng, \
            photo_taken_at, loc_name, loc_text = r

        use_lat = photo_lat or lat
        use_lng = photo_lng or lng
        loc = loc_name or loc_text or "unknown"
        d = date_str or date_approx or "unknown"
        print(f"{sid:<5} {d:<12} {loc[:34]:<35} {use_lat:<10.4f} {use_lng:<11.4f}")

    if args.dry_run:
        print("\n[dry-run] No enrichment performed.")
        return

    print(f"\nEnriching {len(rows)} sessions (timeout: 10s each)...\n")
    success = 0
    failed = 0

    for r in rows:
        sid, date_str, date_approx, lat, lng, photo_lat, photo_lng, \
            photo_taken_at, loc_name, loc_text = r

        use_lat = photo_lat or lat
        use_lng = photo_lng or lng
        loc = loc_name or loc_text or "unknown"

        # Determine datetime
        dt = None
        if photo_taken_at:
            try:
                dt = datetime.fromisoformat(
                    photo_taken_at.replace("Z", "+00:00")
                )
            except Exception:
                pass

        if dt is None:
            d = date_str or date_approx
            if d:
                try:
                    dt = datetime.fromisoformat(f"{d}T12:00:00")
                except Exception:
                    pass

        if dt is None:
            print(f"  [SKIP] Session {sid} — no usable date")
            failed += 1
            continue

        try:
            result = enrich_session_conditions(
                db, sid, use_lat, use_lng, dt,
                timeout_seconds=10.0,
            )
            if result.get("stored"):
                weather = result.get("weather", {})
                anomaly = result.get("anomalies", {})
                flag = anomaly.get("anomaly_flag", "")
                temp = weather.get("air_temp_c")
                pressure = weather.get("pressure_hpa")
                print(f"  ✓ Session {sid} ({loc[:30]}) — "
                      f"{temp}°C, {pressure}hPa, {flag}")
                success += 1
            else:
                print(f"  ✗ Session {sid} — not stored: {result}")
                failed += 1
        except Exception as e:
            print(f"  ✗ Session {sid} — error: {e}")
            failed += 1

    print(f"\nDone: {success} enriched, {failed} failed/skipped.")
    print("Run 'sqlite3 data/fishing.db "
          "\"SELECT session_id, air_temp_c, pressure_hpa, anomaly_flag "
          "FROM session_conditions ORDER BY session_id;\"' to verify.")


if __name__ == "__main__":
    main()
