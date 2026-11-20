"""What a public deployment relies on: admin-only ingest, CORS, docs routes, generic 500s."""

import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FISHBOT_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("FISHBOT_API_KEY", raising=False)
    monkeypatch.delenv("FISHBOT_BIND_HOST", raising=False)
    monkeypatch.delenv("FISHBOT_ENABLE_DOCS", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import src.api.main as main

    main = importlib.reload(main)
    return TestClient(main.app)


def test_ingest_needs_admin_key(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, FISHBOT_API_KEY="secret")
    assert client.post("/ingest", json={"query": "x"}).status_code == 401
    assert (
        client.post("/ingest", json={"query": "x"}, headers={"X-Api-Key": "no"}).status_code == 401
    )


def test_ingest_refuses_when_key_unset(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post("/ingest", json={"query": "x"}).status_code == 503


def test_cors_limited_to_configured_origins(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, FISHBOT_CORS_ORIGINS="https://fishdex.vercel.app/")
    ok = client.get("/health", headers={"Origin": "https://fishdex.vercel.app"})
    assert ok.headers["access-control-allow-origin"] == "https://fishdex.vercel.app"
    other = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_cors_defaults_to_loopback_origins(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    ok = client.get("/health", headers={"Origin": "http://localhost:8765"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:8765"
    ok = client.get("/health", headers={"Origin": "http://127.0.0.1:3000"})
    assert ok.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"


def test_cors_preflight_from_another_origin_is_refused_by_default(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.options(
        "/chat",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers
    # A lookalike host must not slip through the loopback pattern.
    r = client.get("/health", headers={"Origin": "http://localhost.evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_star_is_still_available_when_asked_for(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, FISHBOT_CORS_ORIGINS="*")
    r = client.get("/health", headers={"Origin": "https://anywhere.example"})
    assert r.headers["access-control-allow-origin"] == "*"


def test_warns_when_bound_beyond_loopback_without_cors_origins(monkeypatch, tmp_path, caplog):
    with caplog.at_level("WARNING"):
        _client(monkeypatch, tmp_path, FISHBOT_BIND_HOST="0.0.0.0")
    assert any("FISHBOT_CORS_ORIGINS is unset" in r.getMessage() for r in caplog.records)


def test_no_warning_when_loopback_or_cors_configured(monkeypatch, tmp_path, caplog):
    with caplog.at_level("WARNING"):
        _client(monkeypatch, tmp_path, FISHBOT_BIND_HOST="127.0.0.1")
        _client(
            monkeypatch,
            tmp_path,
            FISHBOT_BIND_HOST="0.0.0.0",
            FISHBOT_CORS_ORIGINS="https://fishdex.vercel.app",
        )
    assert not any("FISHBOT_CORS_ORIGINS" in r.getMessage() for r in caplog.records)


def test_docs_routes_are_off_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("FISHBOT_ENABLE_DOCS", raising=False)
    client = _client(monkeypatch, tmp_path)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_docs_routes_come_back_with_the_flag(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, FISHBOT_ENABLE_DOCS="1")
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_chat_500_hides_the_exception_and_logs_it(monkeypatch, tmp_path, caplog):
    client = _client(monkeypatch, tmp_path)
    import src.agent.chat as chat_module
    import src.api.main as main

    def boom(*_a, **_k):
        raise RuntimeError("no such table: users at /srv/fishdex/data/fishing.db")

    monkeypatch.setattr(chat_module, "run_chat_api", boom)
    monkeypatch.setattr(main, "_reserve_model_spend", lambda *_a, **_k: None)
    main.app.dependency_overrides[main.get_current_user] = lambda: {"id": 1, "username": "t"}
    try:
        with caplog.at_level("ERROR"):
            r = client.post("/chat", json={"message": "hi"}, headers={"X-Request-ID": "req-abc"})
    finally:
        main.app.dependency_overrides.clear()
    assert r.status_code == 500
    body = r.text
    assert "req-abc" in body
    assert "no such table" not in body and "/srv/" not in body and "fishing.db" not in body
    logged = [rec for rec in caplog.records if "req-abc" in rec.getMessage()]
    assert logged and logged[0].exc_info and "no such table" in str(logged[0].exc_info[1])
