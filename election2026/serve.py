"""
serve.py — a tiny stdlib-only local server for the 2026 forecast.

Serves the project root over HTTP so you can, from a browser:
  * open the dashboard          -> http://localhost:8000/dashboard.html
  * read the live forecast data  -> http://localhost:8000/forecast.json
  * hit a always-fresh JSON API  -> http://localhost:8000/api/forecast

CORS headers are added so a separate frontend (any origin) can fetch the data.
No third-party dependencies — just the standard library.

Usage:
    python -m election2026.serve                 # serve on :8000
    python -m election2026.serve --port 9000      # custom port
    python -m election2026.serve --refresh        # regenerate forecast.json first
"""

from __future__ import annotations

import argparse
import json
import os
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)          # dir that contains election2026/ + the HTML
FORECAST_PATH = os.path.join(HERE, "data", "forecast.json")


class Handler(SimpleHTTPRequestHandler):
    """Static file server rooted at the project dir, plus a live JSON route."""

    def end_headers(self):
        # Allow any frontend origin to fetch the data + don't cache stale JSON.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        # Convenience aliases: /forecast.json and /api/forecast both return the
        # freshly-read forecast document regardless of where it lives on disk.
        if self.path in ("/forecast.json", "/api/forecast"):
            return self._serve_forecast()
        return super().do_GET()

    def _serve_forecast(self):
        if not os.path.exists(FORECAST_PATH):
            self._json({"error": "forecast.json이 없습니다 — 먼저 "
                                 "`python3 -m election2026 run`을 실행하세요"},
                       status=404)
            return
        try:
            with open(FORECAST_PATH, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as exc:
            self._json({"error": "could not read forecast.json: %s" % exc}, status=500)
            return
        self._json(doc)

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter logging
        print("[serve] %s - %s" % (self.address_string(), fmt % args))


def _main():
    ap = argparse.ArgumentParser(description="Serve the 2026 forecast locally.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--refresh", action="store_true",
                    help="regenerate forecast.json before serving")
    ap.add_argument("--with-social", action="store_true",
                    help="with --refresh, also pull the slow social sources "
                         "(all three are weighted 0 — see config.TRACK_B)")
    args = ap.parse_args()

    if args.refresh:
        from . import pipeline
        print("[serve] refreshing forecast.json...")
        pipeline.run(skip_sources=(None if args.with_social
                                   else {"youtube", "gdelt", "reddit"}))

    os.chdir(PROJECT_ROOT)
    handler = functools.partial(Handler, directory=PROJECT_ROOT)
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    base = "http://%s:%d" % (args.host, args.port)
    print("[serve] serving %s" % PROJECT_ROOT)
    print("[serve]   dashboard : %s/dashboard.html" % base)
    print("[serve]   data      : %s/forecast.json" % base)
    print("[serve]   live API  : %s/api/forecast" % base)
    print("[serve] Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] stopped")
        httpd.shutdown()


if __name__ == "__main__":
    _main()
