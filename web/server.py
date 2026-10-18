"""Serve Clearcut and analyze a rectangle drawn on the map.

    .venv/bin/python web/server.py
    http://127.0.0.1:8765/web/
"""
import json
import os
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
from store import cached_plot, leaderboard, plot_id, record_plot, rebuild_leaderboard  # noqa: E402

PORT = 8765


def python_bin():
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


PYTHON = python_bin()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/leaderboard":
            self._json(200, leaderboard())
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/analyze":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            bbox = [float(v) for v in body["bbox"]]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            self._json(400, {"error": "Send bbox as [minLon, minLat, maxLon, maxLat]."})
            return
        if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            self._json(400, {"error": "The rectangle is empty."})
            return
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width > 1 or height > 1:
            self._json(400, {"error": "Draw a smaller rectangle. This check covers about 1 degree."})
            return

        hit = cached_plot(bbox)
        if hit is not None:
            record_plot(hit, hit=True)
            self._json(200, {"plot": hit.name, "cached": True})
            return

        plot = ROOT / "data" / "plots" / plot_id(bbox)
        plot.mkdir(parents=True, exist_ok=True)
        bbox_arg = ",".join(str(v) for v in bbox)
        env = os.environ.copy()
        run = subprocess.run(
            [PYTHON, "pipeline/analyze_fast.py", "--bbox", bbox_arg, "--out", str(plot)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if run.returncode != 0:
            detail = (run.stderr or run.stdout or "analysis failed").strip()[-800:]
            self._json(500, {"error": detail})
            return
        record_plot(plot, hit=False)
        self._json(200, {"plot": plot.name, "cached": False})

    def _json(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    os.chdir(ROOT)
    rebuild_leaderboard()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"http://127.0.0.1:{PORT}/web/", flush=True)
    server.serve_forever()
