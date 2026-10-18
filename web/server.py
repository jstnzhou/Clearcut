"""Serve Clearcut and analyze a drawn box or submitted property geometry.

    .venv/bin/python web/server.py
    http://127.0.0.1:8765/web/
"""
import json
import os
import re
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)
PORT = 8765


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE pairs into os.environ without overriding existing values."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


def ring_bbox(ring):
    lons = [pt[0] for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    lats = [pt[1] for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    if not lons or not lats:
        return None
    return [min(lons), min(lats), max(lons), max(lats)]


def geometry_bbox(geometry):
    if not geometry or not isinstance(geometry, dict):
        return None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Polygon" and coords:
        return ring_bbox(coords[0])
    if gtype == "MultiPolygon" and coords:
        boxes = [ring_bbox(poly[0]) for poly in coords if poly]
        boxes = [b for b in boxes if b]
        if not boxes:
            return None
        return [
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        ]
    return None


def slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", (text or "plot").strip()).strip("-").lower()
    return cleaned[:48] or "plot"


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
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "Send JSON with bbox and optional geometry."})
            return

        geometry = body.get("geometry")
        if isinstance(geometry, dict) and geometry.get("type") == "Feature":
            geometry = geometry.get("geometry")
        bbox = body.get("bbox")
        if bbox is None and geometry is not None:
            bbox = geometry_bbox(geometry)
        try:
            bbox = [float(v) for v in bbox]
        except (TypeError, ValueError):
            self._json(400, {"error": "Send bbox as [minLon, minLat, maxLon, maxLat], or a polygon geometry."})
            return
        if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            self._json(400, {"error": "The property bounds are empty."})
            return
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width > 1 or height > 1:
            self._json(400, {"error": "That property is too large. This check covers about 1 degree."})
            return

        lat = (bbox[1] + bbox[3]) / 2
        lon = (bbox[0] + bbox[2]) / 2
        input_method = str(body.get("input_method") or ("upload" if geometry else "draw"))
        custom_name = body.get("name")
        coord = f"{lat:.3f}_{lon:.3f}".replace("-", "m").replace(".", "p")
        if input_method == "draw" and not custom_name:
            plot = ROOT / "data" / "plots" / f"box_{coord}"
        else:
            base = slug(str(custom_name or "property"))
            plot = ROOT / "data" / "plots" / f"{base}_{coord}"
        plot.mkdir(parents=True, exist_ok=True)

        submission = {
            "name": custom_name or plot.name,
            "input_method": input_method,
            "source_filename": body.get("source_filename"),
            "bbox": bbox,
            "geometry": geometry,
            "properties": body.get("properties") or {},
        }
        with (plot / "submission.json").open("w") as f:
            json.dump(submission, f, indent=2)
            f.write("\n")
        if geometry:
            feature = {
                "type": "Feature",
                "properties": dict(submission["properties"]),
                "geometry": geometry,
            }
            feature["properties"]["name"] = submission["name"]
            feature["properties"]["input_method"] = input_method
            with (plot / "submission.geojson").open("w") as f:
                json.dump(feature, f, indent=2)
                f.write("\n")

        bbox_arg = ",".join(str(v) for v in bbox)
        env = os.environ.copy()
        cmd = [str(PYTHON), "pipeline/analyze_fast.py", "--bbox", bbox_arg, "--out", str(plot)]
        run = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
        if run.returncode != 0:
            detail = (run.stderr or run.stdout or "analysis failed").strip()[-800:]
            self._json(500, {"error": detail})
            return
        name = plot.relative_to(ROOT / "data" / "plots").as_posix()
        self._json(200, {
            "plot": name,
            "bbox": bbox,
            "input_method": input_method,
            "submission": f"/data/plots/{name}/submission.json",
        })

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
