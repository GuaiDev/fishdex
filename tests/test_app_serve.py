"""The app (app/) loads its fonts, Leaflet and the unchanged Lavish screens by relative path
out of design/lavish/, so the static server has to be rooted at the repo root."""

import threading
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from app.serve import ROOT, NoCacheHandler


@pytest.fixture()
def base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(NoCacheHandler, directory=str(ROOT)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_app_page_is_served_without_caching(base):
    with urllib.request.urlopen(f"{base}/app/index.html") as res:
        assert res.status == 200
        assert res.headers["Cache-Control"] == "no-cache"


@pytest.mark.parametrize(
    "path",
    [
        "design/lavish/map-assets/vendor/leaflet/leaflet.js",
        "design/lavish/map-assets/vendor/fonts/fraunces.css",
        "design/lavish/fishdex-field-guide.html",
        "design/lavish/fishdex-anglers.html",
        "design/lavish/theme-rebuild.html",
    ],
)
def test_lavish_assets_the_app_links_to_are_reachable(base, path):
    with urllib.request.urlopen(f"{base}/{path}") as res:
        assert res.status == 200
