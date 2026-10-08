#!/usr/bin/env python3
"""Read-only paper-trading dashboard in your browser (ADR-0048).

    python3 scripts/run_dashboard.py            # then open http://127.0.0.1:8765

Shows the 15m chart with fills, entry price and stop line, the current position and its open PnL, the
balance, and recent decisions and closed trades. It only reads `var/` files the paper trader writes; it
has no buttons and cannot place or cancel anything. Binds to this computer only (127.0.0.1).

The page is a React app built with bklit UI charts (source in `dashboard-ui/`). Its build output is
committed in `scripts/dashboard_static/`, so running this needs only Python, not Node.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.monitoring.dashboard import read_snapshot  # noqa: E402
from cointrader.settings import load_paper  # noqa: E402

STATIC = Path(__file__).resolve().parent / "dashboard_static"  # built from dashboard-ui/ (npm run build)
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".woff2": "font/woff2"}


def static_file(url_path: str) -> Optional[Path]:
    """The built page file for a URL path, or None. Never resolves outside the static folder."""
    rel = "index.html" if url_path in ("", "/") else url_path.lstrip("/")
    path = (STATIC / rel).resolve()
    if STATIC.resolve() not in path.parents or not path.is_file():
        return None
    return path


def main() -> int:
    cfg = load_paper()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-dir", type=Path, default=REPO / cfg["state_dir"])
    ap.add_argument("--data-root", type=Path, default=REPO / cfg["data_root"])
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    symbols = list(cfg["symbols"])

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path == "/api/config":
                self._send(200, json.dumps({"symbols": symbols}).encode("utf-8"), "application/json")
            elif url.path == "/api/snapshot":
                symbol = parse_qs(url.query).get("symbol", [symbols[0]])[0]
                if symbol not in symbols:
                    self._send(404, b'{"error": "unknown symbol"}', "application/json")
                    return
                snap = read_snapshot(args.state_dir, args.data_root, symbol)
                self._send(200, json.dumps(snap, ensure_ascii=False, default=str).encode("utf-8"),
                           "application/json; charset=utf-8")
            elif (path := static_file(url.path)) is not None:
                self._send(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"))
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *_args) -> None:  # keep the console quiet
            pass

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"대시보드: http://127.0.0.1:{args.port}  (끄려면 Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
