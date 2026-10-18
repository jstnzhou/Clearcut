"""Plot cache keys and the investigation leaderboard."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLOTS = ROOT / "data" / "plots"
INDEX = ROOT / "data" / "leaderboard.json"
NEAR_IOU = 0.6


def quantize_bbox(bbox):
    return [round(float(v), 3) for v in bbox]


def _fmt(value):
    return f"{value:.3f}".replace("-", "m").replace(".", "p")


def plot_id(bbox):
    min_lon, min_lat, max_lon, max_lat = quantize_bbox(bbox)
    return f"box_{_fmt(min_lon)}_{_fmt(min_lat)}_{_fmt(max_lon)}_{_fmt(max_lat)}"


def legacy_plot_id(bbox):
    lat = (bbox[1] + bbox[3]) / 2
    lon = (bbox[0] + bbox[2]) / 2
    return f"box_{lat:.3f}_{lon:.3f}".replace("-", "m").replace(".", "p")


def iou(a, b):
    left = max(a[0], b[0])
    bottom = max(a[1], b[1])
    right = min(a[2], b[2])
    top = min(a[3], b[3])
    width = max(0.0, right - left)
    height = max(0.0, top - bottom)
    intersection = width * height
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def cached_plot(bbox):
    """Return a plot folder that already has a scored check, or None."""
    candidates = [PLOTS / plot_id(bbox), PLOTS / legacy_plot_id(bbox)]
    for path in candidates:
        if (path / "confidence.json").is_file():
            return path
    matches = []
    for path in PLOTS.iterdir() if PLOTS.is_dir() else []:
        evidence = _read_json(path / "evidence.json")
        if not evidence or not isinstance(evidence.get("bbox"), list) or len(evidence["bbox"]) != 4:
            continue
        if not (path / "confidence.json").is_file():
            continue
        score = iou(bbox, evidence["bbox"])
        if score >= NEAR_IOU:
            matches.append((score, path.name == "kalbar1", path))
    if not matches:
        return None
    matches.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return matches[0][2]


def entry_from_plot(path: Path, hits=None, last_seen=None):
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
    names = {
        "kalbar1": "Kalimantan submission",
        "kalbar_check": "Kalimantan check",
        "speedtest": "Speedtest plot",
    }
    entry = {
        "id": path.name,
        "name": names.get(path.name, f"{lat:.3f}, {lon:.3f}"),
        "bbox": bbox,
        "lat": round(float(lat), 5),
        "lon": round(float(lon), 5),
        "mean_score": round(float(score), 3),
        "gated": bool(conf.get("gated")),
        "model": conf.get("model") or "perceptron-mk1.5",
        "analyzed_at": stamped.isoformat().replace("+00:00", "Z"),
        "hits": 1 if hits is None else hits,
    }
    if last_seen:
        entry["last_seen"] = last_seen
    return entry


def _load_index():
    data = _read_json(INDEX)
    if not isinstance(data, dict) or not isinstance(data.get("plots"), list):
        return {"updated": None, "plots": []}
    return data


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _save_index(data):
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    data["updated"] = _utc_now()
    data["plots"] = sorted(
        data["plots"],
        key=lambda row: (row.get("mean_score") is None, -(row.get("mean_score") or 0)),
    )
    tmp = INDEX.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(INDEX)


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


def record_plot(path: Path, *, hit: bool):
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
    if not INDEX.is_file():
        return rebuild_leaderboard()
    data = _load_index()
    data["plots"] = sorted(
        data["plots"],
        key=lambda row: (row.get("mean_score") is None, -(row.get("mean_score") or 0)),
    )
    return data
