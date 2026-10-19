"""Serve OpenCanopy and analyze a drawn box or submitted property geometry.

    .venv/bin/python web/server.py
    http://127.0.0.1:8765/web/
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone

import numpy as np
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)
PORT = 8765
PLOTS = ROOT / "data" / "plots"
COMPANIES = ROOT / "data" / "companies"
INDEX = ROOT / "data" / "leaderboard.json"
NEAR_IOU = 0.6


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


def _fmt(value):
    return f"{value:.3f}".replace("-", "m").replace(".", "p")


def center_plot_id(bbox):
    lat = (bbox[1] + bbox[3]) / 2
    lon = (bbox[0] + bbox[2]) / 2
    return f"box_{_fmt(lat)}_{_fmt(lon)}"


def corner_plot_id(bbox):
    min_lon, min_lat, max_lon, max_lat = (round(float(v), 3) for v in bbox)
    return f"box_{_fmt(min_lon)}_{_fmt(min_lat)}_{_fmt(max_lon)}_{_fmt(max_lat)}"


def iou(a, b):
    left = max(a[0], b[0])
    bottom = max(a[1], b[1])
    right = min(a[2], b[2])
    top = min(a[3], b[3])
    intersection = max(0.0, right - left) * max(0.0, top - bottom)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _finished(path):
    return path.is_dir() and (path / "confidence.json").is_file()


def contains_bbox(outer, inner, eps=1e-5):
    """True when inner sits inside outer, allowing a tiny rounding gap."""
    try:
        return (
            float(outer[0]) <= float(inner[0]) + eps
            and float(outer[1]) <= float(inner[1]) + eps
            and float(outer[2]) >= float(inner[2]) - eps
            and float(outer[3]) >= float(inner[3]) - eps
        )
    except (TypeError, ValueError, IndexError):
        return False


def cached_plot(bbox):
    """Return the smallest finished plot whose image covers this shape."""
    matches = []
    if PLOTS.is_dir():
        for path in PLOTS.iterdir():
            if not _finished(path):
                continue
            evidence = _read_json(path / "evidence.json")
            stored = evidence.get("bbox") if isinstance(evidence, dict) else None
            if not isinstance(stored, list) or len(stored) != 4:
                continue
            if not contains_bbox(stored, bbox):
                continue
            area = max(0.0, float(stored[2]) - float(stored[0])) * max(0.0, float(stored[3]) - float(stored[1]))
            matches.append((area, path.name != "kalbar1", path))
    if not matches:
        return None
    matches.sort(key=lambda row: (row[0], row[1]))
    return matches[0][2]


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_index():
    data = _read_json(INDEX)
    if not isinstance(data, dict) or not isinstance(data.get("plots"), list):
        return {"updated": None, "plots": []}
    return data


def _save_index(data):
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    data["updated"] = _utc_now()
    data["plots"] = sorted(
        data["plots"],
        key=lambda row: (row.get("plantation_share") is None, -(row.get("plantation_share") or 0)),
    )
    tmp = INDEX.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(INDEX)


def plantation_share(conf):
    """Fraction of the rectangle that is post-2020 forest loss and looks planted."""
    windows = conf.get("windows") or []
    boxes = [w for w in windows if isinstance(w.get("box"), list) and len(w["box"]) == 4]
    if not boxes:
        return None
    width = max(int(w["box"][2]) for w in boxes)
    height = max(int(w["box"][3]) for w in boxes)
    if width <= 0 or height <= 0:
        return None
    grid = np.zeros((height, width), np.float32)
    counted = False
    for window in boxes:
        score = window.get("score")
        loss = window.get("loss_share")
        forest = window.get("forest_share")
        if not isinstance(score, (int, float)) or not isinstance(loss, (int, float)):
            continue
        counted = True
        weight = float(loss) * float(score)
        if isinstance(forest, (int, float)):
            weight *= float(forest)
        if weight <= 0:
            continue
        left, top, right, bottom = (int(v) for v in window["box"])
        left = max(0, min(width, left))
        right = max(left, min(width, right))
        top = max(0, min(height, top))
        bottom = max(top, min(height, bottom))
        grid[top:bottom, left:right] = np.maximum(grid[top:bottom, left:right], weight)
    if not counted:
        return None
    return round(float(grid.mean()), 3)


def entry_from_plot(path, hits=None, last_seen=None):
    conf = _read_json(path / "confidence.json")
    if not conf:
        return None
    score = conf.get("mean_score")
    if not isinstance(score, (int, float)):
        return None
    evidence = _read_json(path / "evidence.json") or {}
    bbox = evidence.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    lat = evidence.get("lat", (bbox[1] + bbox[3]) / 2)
    lon = evidence.get("lon", (bbox[0] + bbox[2]) / 2)
    stamped = datetime.fromtimestamp(path.joinpath("confidence.json").stat().st_mtime, timezone.utc).replace(microsecond=0)
    names = {"kalbar1": "Plot KS-19"}
    submission = _read_json(path / "submission.json") or {}
    custom = submission.get("name") if isinstance(submission, dict) else None
    entry = {
        "id": path.name,
        "name": custom or names.get(path.name, f"{float(lat):.3f}, {float(lon):.3f}"),
        "bbox": bbox,
        "lat": round(float(lat), 5),
        "lon": round(float(lon), 5),
        "mean_score": round(float(score), 3),
        "plantation_share": plantation_share(conf),
        "analyzed_at": stamped.isoformat().replace("+00:00", "Z"),
        "hits": 1 if hits is None else hits,
    }
    if last_seen:
        entry["last_seen"] = last_seen
    return entry


def rebuild_leaderboard():
    prior = {row["id"]: row for row in _load_index()["plots"] if "id" in row}
    plots = []
    if PLOTS.is_dir():
        for path in PLOTS.iterdir():
            if not path.is_dir():
                continue
            old = prior.get(path.name, {})
            entry = entry_from_plot(path, hits=old.get("hits"), last_seen=old.get("last_seen"))
            if entry:
                plots.append(entry)
    data = {"plots": plots}
    _save_index(data)
    return data


def record_plot(path, hit):
    now = _utc_now()
    data = _load_index()
    by_id = {row["id"]: row for row in data["plots"] if "id" in row}
    old = by_id.get(path.name, {})
    hits = int(old.get("hits") or 0) + 1
    entry = entry_from_plot(path, hits=hits, last_seen=now)
    if not entry:
        return None
    if hit:
        entry["analyzed_at"] = old.get("analyzed_at") or entry["analyzed_at"]
    else:
        entry["analyzed_at"] = now
    by_id[path.name] = entry
    data["plots"] = list(by_id.values())
    _save_index(data)
    return entry


def leaderboard():
    data = _load_index()
    stale = not INDEX.is_file() or any("plantation_share" not in row for row in data["plots"])
    if stale:
        return rebuild_leaderboard()
    data["plots"] = sorted(
        data["plots"],
        key=lambda row: (row.get("plantation_share") is None, -(row.get("plantation_share") or 0)),
    )
    return data


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


def find_uml_workbook():
    candidates = []
    env = os.environ.get("UML_XLSX")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(ROOT / "data" / "UML.xlsx")
    candidates.append(Path.home() / "Downloads" / "UML.xlsx")
    for path in candidates:
        if path.is_file():
            return path
    return None


def company_slug(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:48] or "company"


def _company_file(company_id):
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", company_id or ""):
        return None
    return COMPANIES / f"{company_id}.json"


def list_companies():
    if not COMPANIES.is_dir():
        return []
    rows = []
    for path in sorted(COMPANIES.glob("*.json")):
        data = _read_json(path)
        if isinstance(data, dict) and data.get("id") and isinstance(data.get("farms"), list):
            rows.append(data)
    rows.sort(key=lambda row: (row.get("name") or "").lower())
    return rows


def save_company(data):
    path = _company_file(data.get("id"))
    if path is None:
        raise ValueError("Invalid company id.")
    COMPANIES.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def import_company_pdf(pdf_bytes, filename):
    uml = find_uml_workbook()
    if uml is None:
        raise ValueError("UML.xlsx was not found. Put it in Downloads or data/UML.xlsx.")
    filename = Path(filename or "upload.pdf").name
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(pdf_bytes)
        tmp.close()
        run = subprocess.run(
            [str(PYTHON), str(ROOT / "web" / "pdf_mills.py"), "parse", tmp.name, str(uml), filename],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    if run.returncode != 0:
        detail = (run.stderr or run.stdout or "Could not read that PDF.").strip().splitlines()
        raise ValueError(detail[-1][:500] if detail else "Could not read that PDF.")
    try:
        parsed = json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("The PDF parser returned invalid data.") from exc
    name = str(parsed.get("company") or "Company").strip()[:80] or "Company"
    company_id = company_slug(name)
    existing_path = _company_file(company_id)
    existing = _read_json(existing_path) if existing_path and existing_path.is_file() else None
    if not isinstance(existing, dict):
        existing = None
    display = existing.get("name") if existing and existing.get("renamed") else name
    farms = parsed.get("farms") or []
    record = {
        "id": company_id,
        "name": display or name,
        "renamed": bool(existing and existing.get("renamed")),
        "source_filename": filename,
        "created": (existing or {}).get("created") or _utc_now(),
        "updated": _utc_now(),
        "mill_count": parsed.get("mill_count") or len(farms),
        "resolved_count": parsed.get("resolved_count") or 0,
        "unresolved_count": parsed.get("unresolved_count") or 0,
        "farms": farms,
    }
    return save_company(record)


def rename_company(company_id, name):
    path = _company_file(str(company_id or ""))
    if path is None or not path.is_file():
        raise ValueError("That company was not found.")
    data = _read_json(path)
    if not isinstance(data, dict):
        raise ValueError("That company file is unreadable.")
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    if not cleaned:
        raise ValueError("The company name cannot be empty.")
    data["name"] = cleaned[:80]
    data["renamed"] = True
    data["updated"] = _utc_now()
    return save_company(data)


def run_pipeline(steps, env, runner=None, fatal_until=1):
    """Run analysis steps. Only steps before fatal_until can fail the request.

    The plantation lookup is appended after the satellite check. Its failure is
    logged and the request still succeeds.
    """
    runner = runner or subprocess.run
    for index, step in enumerate(steps):
        try:
            run = runner(step, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired as exc:
            detail = exc.stderr or exc.stdout or "timed out"
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", "replace")
            detail = str(detail).strip()[-800:]
            if index < fatal_until:
                return False, detail or "analysis timed out"
            print(detail or "investigation timed out", file=sys.stderr)
            continue
        if getattr(run, "returncode", 1) != 0:
            detail = (getattr(run, "stderr", None) or getattr(run, "stdout", None) or "analysis failed")
            detail = str(detail).strip()[-800:]
            if index < fatal_until:
                return False, detail
            print(detail, file=sys.stderr)
    return True, ""


def is_rectangle_geometry(geometry, bbox):
    """True when geometry is missing or is essentially the axis-aligned bbox."""
    if not isinstance(geometry, dict):
        return True
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    ring = None
    if gtype == "Polygon" and coords:
        ring = coords[0]
    elif gtype == "MultiPolygon" and coords and len(coords) == 1 and coords[0]:
        ring = coords[0][0]
    if not ring or len(ring) < 4:
        return True
    # Closed ring with only the four bbox corners (order may vary).
    pts = {(round(float(pt[0]), 6), round(float(pt[1]), 6)) for pt in ring if isinstance(pt, (list, tuple)) and len(pt) >= 2}
    if len(pts) != 4:
        return False
    min_lon, min_lat, max_lon, max_lat = (round(float(v), 6) for v in bbox)
    expected = {
        (min_lon, min_lat),
        (min_lon, max_lat),
        (max_lon, min_lat),
        (max_lon, max_lat),
    }
    return pts == expected


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
        if parsed.path == "/api/companies":
            self._json(200, {"companies": list_companies()})
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/companies/import":
            self._import_pdf()
            return
        if path == "/api/companies/rename":
            self._rename_company_request()
            return
        if path == "/api/investigate":
            self._investigate_plot()
            return
        if path != "/api/analyze":
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

        input_method = str(body.get("input_method") or ("upload" if geometry else "draw"))
        rectangular = is_rectangle_geometry(geometry, bbox)
        # A saved image can answer this check when it covers the drawn shape.
        # The browser keeps that shape and shows only the covered portion.
        hit = cached_plot(bbox)
        if hit is not None:
            record_plot(hit, hit=True)
            self._json(200, {"plot": hit.name, "cached": True})
            return

        custom_name = body.get("name")
        lat = (bbox[1] + bbox[3]) / 2
        lon = (bbox[0] + bbox[2]) / 2
        coord = f"{lat:.3f}_{lon:.3f}".replace("-", "m").replace(".", "p")
        if custom_name:
            plot = PLOTS / f"{slug(str(custom_name))}_{coord}"
        elif input_method in ("draw", "draw_box") and rectangular:
            plot = PLOTS / center_plot_id(bbox)
        elif input_method == "draw_shape" or (geometry and not rectangular):
            plot = PLOTS / f"shape_{coord}"
        else:
            plot = PLOTS / f"{slug('property')}_{coord}"
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
        geometry_path = None
        if geometry:
            feature = {
                "type": "Feature",
                "properties": dict(submission["properties"]),
                "geometry": geometry,
            }
            feature["properties"]["name"] = submission["name"]
            feature["properties"]["input_method"] = input_method
            geometry_path = plot / "submission.geojson"
            with geometry_path.open("w") as f:
                json.dump(feature, f, indent=2)
                f.write("\n")

        bbox_arg = ",".join(str(v) for v in bbox)
        env = os.environ.copy()
        cmd = [str(PYTHON), "pipeline/analyze_fast.py", "--bbox", bbox_arg, "--out", str(plot)]
        if geometry_path is not None and not rectangular:
            cmd.extend(["--geometry", str(geometry_path)])
        ok, detail = run_pipeline([cmd], env)
        if not ok:
            self._json(500, {"error": detail})
            return
        record_plot(plot, hit=False)
        self._json(200, {
            "plot": plot.name,
            "cached": False,
            "bbox": bbox,
            "input_method": input_method,
            "submission": f"/data/plots/{plot.name}/submission.json",
        })

    def _investigate_plot(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "Send JSON with plot."})
            return
        plot_id = str(body.get("plot") or "")
        if not re.fullmatch(r"[A-Za-z0-9_]+", plot_id):
            self._json(400, {"error": "Unknown plot."})
            return
        plot = PLOTS / plot_id
        if not (plot / "confidence.json").is_file():
            self._json(404, {"error": "That plot has no score yet."})
            return
        run = subprocess.run(
            [str(PYTHON), "pipeline/investigate.py", str(plot)],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=180,
        )
        written = plot / "investigation.json"
        if not written.is_file():
            detail = (run.stderr or run.stdout or "Source lookup did not write a report.").strip()[-800:]
            self._json(500, {"error": detail})
            return
        self._json(200, json.loads(written.read_text(encoding="utf-8")))

    def _read_body(self, limit):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return None
        if length <= 0 or length > limit:
            return None
        return self.rfile.read(length)

    def _import_pdf(self):
        raw = self._read_body(40_000_000)
        if not raw:
            self._json(400, {"error": "Upload a PDF."})
            return
        filename = unquote(self.headers.get("X-Filename") or "upload.pdf")
        try:
            record = import_company_pdf(raw, filename)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        except subprocess.TimeoutExpired:
            self._json(500, {"error": "Reading that PDF took too long."})
            return
        self._json(200, {
            "id": record["id"],
            "name": record["name"],
            "mill_count": record["mill_count"],
            "resolved_count": record["resolved_count"],
            "unresolved_count": record["unresolved_count"],
        })

    def _rename_company_request(self):
        raw = self._read_body(100_000)
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "Send JSON with id and name."})
            return
        try:
            record = rename_company(str(body.get("id") or ""), str(body.get("name") or ""))
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, {"id": record["id"], "name": record["name"]})

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
