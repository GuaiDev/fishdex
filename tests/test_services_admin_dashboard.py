"""Tests for the admin dashboard aggregation service."""

from datetime import datetime, timedelta

from src.services.admin_dashboard import build_dashboard


def _make_db(fast_db):
    return fast_db


def _seed_users(db, n=2):
    """Adds n *additional* users on top of the default user (id=1, 'jason')
    get_db()'s ensure_schema always seeds — start at id=2 to avoid colliding."""
    for i in range(2, n + 2):
        db.execute(
            "INSERT INTO users (id, username, display_name, role) VALUES (?, ?, ?, 'beta')",
            [i, f"user{i}", f"User {i}"],
        )
    db.conn.commit()


def _seed_chat_message(db, role, created_at, session_id="s1", user_id=1):
    db.execute(
        "INSERT INTO chat_sessions (session_id, user_id) VALUES (?, ?) "
        "ON CONFLICT(session_id) DO NOTHING",
        [session_id, user_id],
    )
    db.execute(
        "INSERT INTO chat_messages (session_id, role, content, turn_index, created_at, user_id) "
        "VALUES (?, ?, 'hi', 0, ?, ?)",
        [session_id, role, created_at, user_id],
    )
    db.conn.commit()


def _seed_tool_usage(db, tool_name, success=1):
    db.execute("INSERT INTO tool_usage (tool_name, success) VALUES (?, ?)", [tool_name, success])
    db.conn.commit()


def _seed_api_usage(db, model, input_tokens, output_tokens, timestamp=None, user_id=1):
    from src.llm.pricing import estimate_usd

    db.execute(
        "INSERT INTO llm_usage (ts, user_id, caller, role, model, input_tokens, output_tokens, "
        "est_usd) VALUES (COALESCE(?, datetime('now')), ?, 'test', 'chat', ?, ?, ?, ?)",
        [
            timestamp,
            user_id,
            model,
            input_tokens,
            output_tokens,
            estimate_usd(model, input_tokens, output_tokens),
        ],
    )
    db.conn.commit()


def _seed_synthesis(db, cache_key, location_name, lat=None, lng=None, hit_count=0):
    db.execute(
        "INSERT INTO segment_synthesis (cache_key, lat, lng, location_name, synthesis, hit_count) "
        "VALUES (?, ?, ?, ?, 'test synthesis', ?)",
        [cache_key, lat, lng, location_name, hit_count],
    )
    db.conn.commit()


# --- users ---


def test_total_users_count(fast_db):
    db = _make_db(fast_db)
    _seed_users(db, n=3)
    result = build_dashboard(db)
    assert result["users"]["total_users"] == 4  # 3 seeded + the default admin user


def test_messages_sent_windowed_correctly(fast_db):
    db = _make_db(fast_db)
    _seed_users(db, n=1)
    now = datetime.now()
    _seed_chat_message(db, "user", (now - timedelta(days=1)).isoformat())
    _seed_chat_message(db, "user", (now - timedelta(days=10)).isoformat())
    _seed_chat_message(db, "user", (now - timedelta(days=40)).isoformat())
    _seed_chat_message(db, "assistant", now.isoformat())  # not counted — assistant reply

    result = build_dashboard(db)
    msgs = result["users"]["messages_sent"]
    assert msgs["last_7_days"] == 1
    assert msgs["last_30_days"] == 2
    assert msgs["all_time"] == 3


# --- tool usage ---


def test_tool_usage_frequency_and_failures(fast_db):
    db = _make_db(fast_db)
    _seed_tool_usage(db, "get_conditions")
    _seed_tool_usage(db, "get_conditions")
    _seed_tool_usage(db, "get_conditions", success=0)
    _seed_tool_usage(db, "log_trip")

    result = build_dashboard(db)
    by_name = {t["tool_name"]: t for t in result["tool_usage"]}
    assert by_name["get_conditions"]["calls"] == 3
    assert by_name["get_conditions"]["failures"] == 1
    assert by_name["log_trip"]["calls"] == 1
    # Sorted by call count descending
    assert result["tool_usage"][0]["tool_name"] == "get_conditions"


# --- ingest status ---


def test_ingest_status_observations_by_source_and_jurisdiction(fast_db):
    db = _make_db(fast_db)
    db.execute(
        "INSERT INTO observations (observation_id, species, lat, lng, observed_on, "
        "jurisdiction, source) "
        "VALUES (1, 'Test', 43.0, -79.0, '2026-01-01', 'CA-ON', 'iNaturalist')"
    )
    db.execute(
        "INSERT INTO observations (observation_id, species, lat, lng, observed_on, "
        "jurisdiction, source) "
        "VALUES (2, 'Test', 49.0, -122.0, '2026-01-01', 'CA-BC', 'FISS')"
    )
    db.conn.commit()

    result = build_dashboard(db)
    obs = {
        (r["source"], r["jurisdiction"]): r["count"]
        for r in result["ingest_status"]["observations"]
    }
    assert obs[("iNaturalist", "CA-ON")] == 1
    assert obs[("FISS", "CA-BC")] == 1


def test_ingest_status_gbif_labeled_correctly(fast_db):
    db = _make_db(fast_db)
    db.execute(
        "INSERT INTO gbif_observations (gbif_key, species, lat, lng, jurisdiction) "
        "VALUES (1, 'Test', 43.0, -79.0, 'CA-ON')"
    )
    db.conn.commit()

    result = build_dashboard(db)
    gbif = result["ingest_status"]["gbif_observations"]
    assert gbif == [{"source": "GBIF", "jurisdiction": "CA-ON", "count": 1}]


# --- top locations ---


def test_top_locations_groups_name_only_entries(fast_db):
    """Real usage in this project is 100% name-only synthesis cache entries
    (no lat/lng) — a lat/lng-only grouping would always return empty."""
    db = _make_db(fast_db)
    _seed_synthesis(db, "name:willoway park", "Willoway Park", hit_count=5)
    _seed_synthesis(db, "name:bronte creek", "Bronte Creek", hit_count=0)

    result = build_dashboard(db)
    names = [loc["sample_location_name"] for loc in result["top_locations"]]
    assert "Willoway Park" in names
    assert "Bronte Creek" in names
    # Willoway (hit_count=5 -> 6 queries) ranks above Bronte (0 -> 1 query)
    assert result["top_locations"][0]["sample_location_name"] == "Willoway Park"
    assert result["top_locations"][0]["query_count"] == 6


def test_top_locations_clusters_nearby_coordinates(fast_db):
    db = _make_db(fast_db)
    _seed_synthesis(db, "geo:43.467,-79.687", "Oakville A", lat=43.4671, lng=-79.6871, hit_count=2)
    _seed_synthesis(db, "geo:43.468,-79.688", "Oakville B", lat=43.4682, lng=-79.6882, hit_count=1)

    result = build_dashboard(db)
    # Both round to (43.5, -79.7) at 1-decimal clustering -> merged into one entry
    assert len(result["top_locations"]) == 1
    assert result["top_locations"][0]["query_count"] == 5  # (2+1) + (1+1)


def test_top_locations_respects_limit(fast_db):
    db = _make_db(fast_db)
    for i in range(15):
        _seed_synthesis(db, f"name:place{i}", f"Place {i}", hit_count=i)
    result = build_dashboard(db)
    assert len(result["top_locations"]) == 10


# --- api cost estimate ---


def test_api_cost_estimate_sonnet_vs_haiku(fast_db):
    db = _make_db(fast_db)
    _seed_api_usage(db, "claude-sonnet-4-6", 1_000_000, 1_000_000)
    _seed_api_usage(db, "claude-haiku-4-5-20251001", 1_000_000, 1_000_000)

    result = build_dashboard(db)
    by_model = {m["model"]: m for m in result["api_cost_estimate"]["all_time"]["by_model"]}
    # Sonnet: $3 input + $15 output = $18; Haiku: $1 + $5 = $6
    assert by_model["claude-sonnet-4-6"]["estimated_usd"] == 18.0
    assert by_model["claude-haiku-4-5-20251001"]["estimated_usd"] == 6.0
    assert result["api_cost_estimate"]["all_time"]["total_usd"] == 24.0


def test_api_cost_estimate_unknown_model_not_silently_priced(fast_db):
    db = _make_db(fast_db)
    _seed_api_usage(db, "some-future-model-nobody-has-priced-yet", 1_000_000, 1_000_000)

    result = build_dashboard(db)
    all_time = result["api_cost_estimate"]["all_time"]
    assert all_time["unpriced_calls"] == 1
    assert all_time["by_model"][0]["estimated_usd"] is None
    assert all_time["total_usd"] == 0.0


def test_api_cost_estimate_last_30_days_excludes_older_calls(fast_db):
    db = _make_db(fast_db)
    old = (datetime.now() - timedelta(days=60)).isoformat()
    _seed_api_usage(db, "claude-sonnet-4-6", 1_000_000, 0, timestamp=old)
    _seed_api_usage(db, "claude-sonnet-4-6", 1_000_000, 0)

    result = build_dashboard(db)
    assert result["api_cost_estimate"]["last_30_days"]["total_usd"] == 3.0  # only the recent call
    assert result["api_cost_estimate"]["all_time"]["total_usd"] == 6.0  # both calls


def test_dashboard_does_not_crash_on_empty_database(fast_db):
    db = _make_db(fast_db)
    result = build_dashboard(db)
    assert result["users"]["total_users"] == 1  # the default admin user get_db() seeds
    assert result["top_locations"] == []
    assert result["tool_usage"] == []


# --- catch stats ---


def _seed_session_and_stop(db):
    session_id = db["sessions"].insert({"date": "2026-06-01"}).last_pk
    stop_id = (
        db["stops"]
        .insert(
            {
                "session_id": session_id,
                "location_text": "Bronte Creek",
                "species_caught": "[]",
            }
        )
        .last_pk
    )
    return session_id, stop_id


def test_catch_stats_empty_database_has_no_crash(fast_db):
    db = _make_db(fast_db)
    result = build_dashboard(db)["catch_stats"]
    assert result["total_catches"] == 0
    assert result["by_species"] == []
    assert result["personal_bests"] == {"users_with_a_pb": 0, "top_10_largest": []}
    assert result["avg_catches_per_session"] == 0.0


def test_catch_stats_total_and_by_species(fast_db):
    from src.storage.catches import insert_catch

    db = _make_db(fast_db)
    session_id, stop_id = _seed_session_and_stop(db)
    insert_catch(db, stop_id=stop_id, session_id=session_id, user_id=1, species="creek chub")
    insert_catch(db, stop_id=stop_id, session_id=session_id, user_id=1, species="creek chub")
    insert_catch(db, stop_id=stop_id, session_id=session_id, user_id=1, species="rock bass")

    result = build_dashboard(db)["catch_stats"]
    assert result["total_catches"] == 3
    by_species = {r["species"]: r["count"] for r in result["by_species"]}
    assert by_species == {"creek chub": 2, "rock bass": 1}
    assert result["by_species"][0]["species"] == "creek chub"  # sorted by count desc


def test_catch_stats_by_species_respects_top_10_limit(fast_db):
    from src.storage.catches import insert_catch

    db = _make_db(fast_db)
    session_id, stop_id = _seed_session_and_stop(db)
    for i in range(15):
        insert_catch(db, stop_id=stop_id, session_id=session_id, user_id=1, species=f"species{i}")

    result = build_dashboard(db)["catch_stats"]
    assert len(result["by_species"]) == 10


def test_catch_stats_by_species_sums_count_not_rows(fast_db):
    """A single catch card logged as 'x3' (one row, count=3) must contribute
    3 to its species' total, not 1 — this was the original bug: a real
    3-fish catch card ranked *below* two separate 1-fish log entries."""
    from src.storage.catches import insert_catch

    db = _make_db(fast_db)
    session_id, stop_id = _seed_session_and_stop(db)
    insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=1,
        species="shorthead redhorse",
        count=3,
    )
    insert_catch(db, stop_id=stop_id, session_id=session_id, user_id=1, species="creek chub")
    insert_catch(db, stop_id=stop_id, session_id=session_id, user_id=1, species="creek chub")

    result = build_dashboard(db)["catch_stats"]
    assert result["total_catches"] == 5  # 3 + 1 + 1
    by_species = {r["species"]: r["count"] for r in result["by_species"]}
    assert by_species["shorthead redhorse"] == 3
    assert by_species["creek chub"] == 2
    # Outranks creek chub now that it correctly counts 3 fish, not 1 row.
    assert result["by_species"][0]["species"] == "shorthead redhorse"


def test_catch_stats_by_species_merges_case_variants(fast_db):
    """Species logged with different casing (e.g. a directly-typed structured
    catch vs. one that came through NL parsing) must merge into one group,
    not silently split a species' true total across two dashboard rows."""
    from src.storage.catches import insert_catch

    db = _make_db(fast_db)
    session_id, stop_id = _seed_session_and_stop(db)
    insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=1,
        species="Shorthead Redhorse",
        count=3,
    )
    insert_catch(
        db,
        stop_id=stop_id,
        session_id=session_id,
        user_id=1,
        species="shorthead redhorse",
        count=1,
    )

    result = build_dashboard(db)["catch_stats"]
    assert len(result["by_species"]) == 1
    assert result["by_species"][0]["count"] == 4


def test_catch_stats_personal_bests_count_and_top_10(fast_db):
    from src.storage.catches import insert_catch, update_personal_best_if_higher

    db = _make_db(fast_db)
    _seed_users(db, n=1)  # user id 2, on top of default user id 1
    session_id, stop_id = _seed_session_and_stop(db)

    c1 = insert_catch(
        db, stop_id=stop_id, session_id=session_id, user_id=1, species="shorthead redhorse"
    )
    update_personal_best_if_higher(
        db, user_id=1, species="shorthead redhorse", size_cm=38.0, catch_id=c1
    )

    c2 = insert_catch(db, stop_id=stop_id, session_id=session_id, user_id=2, species="common carp")
    update_personal_best_if_higher(db, user_id=2, species="common carp", size_cm=55.0, catch_id=c2)

    result = build_dashboard(db)["catch_stats"]["personal_bests"]
    assert result["users_with_a_pb"] == 2
    top = {r["species"]: r for r in result["top_10_largest"]}
    assert top["common carp"]["biggest_size_cm"] == 55.0
    assert top["common carp"]["username"] == "user2"
    assert top["shorthead redhorse"]["biggest_size_cm"] == 38.0
    # Sorted largest first
    assert result["top_10_largest"][0]["species"] == "common carp"


def test_catch_stats_personal_bests_top_10_limit(fast_db):
    from src.storage.catches import insert_catch, update_personal_best_if_higher

    db = _make_db(fast_db)
    session_id, stop_id = _seed_session_and_stop(db)
    for i in range(15):
        c = insert_catch(
            db, stop_id=stop_id, session_id=session_id, user_id=1, species=f"species{i}"
        )
        update_personal_best_if_higher(
            db, user_id=1, species=f"species{i}", size_cm=float(i), catch_id=c
        )

    result = build_dashboard(db)["catch_stats"]["personal_bests"]
    assert len(result["top_10_largest"]) == 10


def test_catch_stats_avg_catches_per_session(fast_db):
    from src.storage.catches import insert_catch

    db = _make_db(fast_db)
    s1, stop1 = _seed_session_and_stop(db)
    s2, stop2 = _seed_session_and_stop(db)
    insert_catch(db, stop_id=stop1, session_id=s1, user_id=1, species="creek chub")
    insert_catch(db, stop_id=stop1, session_id=s1, user_id=1, species="rock bass")
    insert_catch(db, stop_id=stop2, session_id=s2, user_id=1, species="smallmouth bass")

    result = build_dashboard(db)["catch_stats"]
    assert result["avg_catches_per_session"] == 1.5  # 3 catches / 2 sessions


def test_api_cost_breakdowns_preserve_unknown_and_partial_estimates(tmp_path):
    db = _make_db(tmp_path)
    _seed_users(db, n=2)
    for caller, user_id, price in [
        ("unknown", 1, None),
        ("mixed", 2, None),
        ("mixed", 2, 0.25),
        ("free", 3, 0.0),
    ]:
        db["llm_usage"].insert(
            {
                "caller": caller,
                "user_id": user_id,
                "role": "router",
                "model": "test",
                "est_usd": price,
            }
        )
    db["llm_usage"].insert(
        {
            "caller": "unknown",
            "user_id": 1,
            "role": "router",
            "model": "test",
            "est_usd": 1.0,
            "ts": "2000-01-01",
        }
    )

    costs = build_dashboard(db)["api_cost_estimate"]

    for breakdown, key, ids in [
        ("by_caller_30_days", "caller", ("unknown", "mixed", "free")),
        ("by_user_30_days", "user_id", (1, 2, 3)),
    ]:
        groups = {row[key]: row for row in costs[breakdown]}
        for group_id, calls, estimate, unpriced in [
            (ids[0], 1, None, 1),
            (ids[1], 2, 0.25, 1),
            (ids[2], 1, 0.0, 0),
        ]:
            assert groups[group_id] == {
                key: group_id,
                "calls": calls,
                "estimated_usd": estimate,
                "unpriced_calls": unpriced,
            }
