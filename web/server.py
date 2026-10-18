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

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"
PORT = 8765


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        if self.path != "/api/analyze":
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

        lat = (bbox[1] + bbox[3]) / 2
        lon = (bbox[0] + bbox[2]) / 2
        plot = ROOT / "data" / "plots" / f"box_{lat:.3f}_{lon:.3f}".replace("-", "m").replace(".", "p")
        plot.mkdir(parents=True, exist_ok=True)
        bbox_arg = ",".join(str(v) for v in bbox)
        env = os.environ.copy()
        cmd = [str(PYTHON), "pipeline/analyze_fast.py", "--bbox", bbox_arg, "--out", str(plot)]
        if not body.get("perceptron", True):
            cmd.append("--no-perceptron")
        if not body.get("forest", True):
            cmd.append("--no-forest")
        if not body.get("loss", True):
            cmd.append("--no-loss")
        steps = [cmd]
        for cmd in steps:
            run = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
            if run.returncode != 0:
                detail = (run.stderr or run.stdout or "analysis failed").strip()[-800:]
                self._json(500, {"error": detail})
                return
        name = plot.relative_to(ROOT / "data" / "plots").as_posix()
        self._json(200, {"plot": name})

    def _json(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    os.chdir(ROOT)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"http://127.0.0.1:{PORT}/web/", flush=True)
    server.serve_forever()
