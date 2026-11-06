"""What a public deployment relies on: admin-only ingest and an origin-restricted CORS list."""

import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FISHBOT_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("FISHBOT_API_KEY", raising=False)
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


def test_cors_defaults_to_any_origin_for_local_use(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/health", headers={"Origin": "http://localhost:8765"})
    assert r.headers["access-control-allow-origin"] == "*"
