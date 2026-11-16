import json
import subprocess
import sys


def test_serve_emits_json_request_logs(tmp_path):
    script = """
import logging
import sys
import uvicorn
from typer.testing import CliRunner
from src.cli.main import app
from src.storage import database

database.DB_PATH = __import__('pathlib').Path(sys.argv[1])

def run(application, **kwargs):
    captured = []
    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(record)
    handler = Capture()
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)
    logging.getLogger().addHandler(handler)
    from src.api.main import app
    from fastapi.testclient import TestClient
    response = TestClient(app).get('/health/live', headers={'X-Request-ID': 'cli-test'})
    assert response.status_code == 200
    assert handler in logging.getLogger().handlers
    assert handler.formatter is formatter
    assert any(record.getMessage() == 'http_request' for record in captured)

uvicorn.run = run
result = CliRunner().invoke(app, ['serve'])
sys.stdout.write(result.output)
if result.exception:
    raise result.exception
assert result.exit_code == 0
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "cli.db")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    request = next(record for record in records if record.get("message") == "http_request")
    assert request["request_id"] == "cli-test"
    assert request["route"] == "/health/live"
    assert request["status"] == 200
    assert request["latency_ms"] >= 0
    assert request["user_hash"] is None
