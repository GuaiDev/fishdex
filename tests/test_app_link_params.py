"""Runs the Node test for the web app's link-parameter handling (skipped without node)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_link_params_node_suite():
    r = subprocess.run(
        ["node", "--test", str(ROOT / "tests/js/link_params.test.mjs")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr
