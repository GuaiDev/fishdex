"""Serve the repo root so the app (app/) can reach the Lavish assets (design/lavish/).

    python3 app/serve.py            # http://localhost:8765/app/
    python3 app/serve.py 9000       # another port

Plain files, no build step. Caching is switched off so an edit shows on a normal reload.
"""

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    handler = partial(NoCacheHandler, directory=str(ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"FishDex app: http://localhost:{port}/app/  (API expected at http://localhost:8000)")
    server.serve_forever()


if __name__ == "__main__":
    main()
