"""Coarse global index of 2020 forest that later lost trees.

For each 10° Hansen/JRC tile, read both rasters at 1000×1000 (0.01°, about 1 km)
with nearest-neighbor resampling, then average 10×10 blocks down to 0.1° cells.
A sample counts as disappeared forest when Hansen lossyear > 20 and JRC GFC2020
is forest (pixel value 1). Cell share and tile loss_share are area shares of that
0.01° sample grid (100 samples per 0.1° cell), not full-resolution 10–30 m area.
The GeoTIFFs have no overviews and are striped LZW, so a 200×200 read costs about
the same as 1000×1000; the finer sample is the one this script uses.

Ocean and unpublished tiles are recorded as missing and skipped. Completed lines
in data/global/forest_loss_index.jsonl are not repeated, so a rerun resumes.

Usage:
    python pipeline/forest_loss_index.py --tile 00N_110E
    python pipeline/forest_loss_index.py --workers 3
"""
import os

os.environ.setdefault("GDAL_HTTP_TIMEOUT", "90")
os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "20")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "4")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")
os.environ.setdefault("CPL_VSIL_CURL_CHUNK_SIZE", "8388608")

import argparse
import fcntl
import heapq
import json
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling

HANSEN_VERSION = "GFC-2024-v1.12"
SAMPLE = 1000
CELLS_PER_SIDE = 100
BLOCK = SAMPLE // CELLS_PER_SIDE
CELL_DEG = 0.1
SAMPLE_DEG = 0.01
# One of 100 samples. Compare with > 0 so float rounding cannot drop 1/100.
CELL_THRESHOLD = 0.01
METHOD = "area_share_of_0.01deg_nearest_samples_reduced_to_0.1deg"
LAT_TOPS = list(range(80, -60, -10))
LON_LEFTS = list(range(-180, 180, 10))
LAT0 = 80.0
LON0 = -180.0
GRID_ROWS = 1400  # 80N .. 60S at 0.1°
GRID_COLS = 3600  # -180 .. 180 at 0.1°

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "global"
JSONL_PATH = OUT_DIR / "forest_loss_index.jsonl"
SUMMARY_PATH = OUT_DIR / "forest_loss_summary.json"
PNG_PATH = OUT_DIR / "forest_loss_overview.png"
LOCK_PATH = OUT_DIR / ".forest_loss_index.lock"


def hansen_name(top, left):
    ns = f"{abs(top):02d}{'N' if top >= 0 else 'S'}"
    ew = f"{abs(left):03d}{'E' if left >= 0 else 'W'}"
    return f"{ns}_{ew}"


def parse_hansen_tile(name):
    ns, ew = name.split("_")
    top = int(ns[:2]) if ns[2] == "N" else -int(ns[:2])
    left = int(ew[:3]) if ew[3] == "E" else -int(ew[:3])
    return top, left


def hansen_url(top, left):
    name = hansen_name(top, left)
    return (
        "/vsicurl/https://storage.googleapis.com/earthenginepartners-hansen/"
        f"{HANSEN_VERSION}/Hansen_{HANSEN_VERSION}_lossyear_{name}.tif"
    )


def jrc_url(top, left):
    ns = f"{'N' if top >= 0 else 'S'}{abs(top)}"
    ew = f"{'E' if left >= 0 else 'W'}{abs(left)}"
    return (
        "/vsicurl/https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/FOREST/"
        f"GFC2020/LATEST/tiles/JRC_GFC2020_V4_{ns}_{ew}.tif"
    )


def tile_tasks():
    """Equator-outward so forested latitudes land in the map early."""
    tops = sorted(LAT_TOPS, key=lambda lat: (abs(lat), -lat))
    tasks = []
    for top in tops:
        for left in LON_LEFTS:
            tasks.append((top, left))
    return tasks


def is_missing(exc):
    msg = str(exc).lower()
    return any(
        token in msg
        for token in ("404", "not found", "no such file", "does not exist", "400")
    )


def read_sample(url):
    last = None
    for attempt in range(3):
        try:
            with rasterio.open(url) as src:
                arr = src.read(1, out_shape=(SAMPLE, SAMPLE), resampling=Resampling.nearest)
                bounds = [src.bounds.left, src.bounds.bottom, src.bounds.right, src.bounds.top]
            return arr, bounds
        except Exception as exc:
            last = exc
            if is_missing(exc) or attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise last


def block_mean(mask):
    view = np.asarray(mask, dtype=np.float32).reshape(CELLS_PER_SIDE, BLOCK, CELLS_PER_SIDE, BLOCK)
    return view.mean(axis=(1, 3))


def empty_record(top, left, status, error=None):
    west, south, east, north = float(left), float(top - 10), float(left + 10), float(top)
    record = {
        "tile": hansen_name(top, left),
        "bounds": [west, south, east, north],
        "status": status,
        "n_cells": 0,
        "n_loss_cells": 0,
        "loss_share": None,
        "cells": [],
        "grid_deg": CELL_DEG,
        "sample_deg": SAMPLE_DEG,
        "method": METHOD,
    }
    if error:
        record["error"] = error[:300]
    return record


def process_tile(top, left):
    started = time.time()
    name = hansen_name(top, left)
    try:
        hansen, bounds = read_sample(hansen_url(top, left))
        jrc, jrc_bounds = read_sample(jrc_url(top, left))
    except Exception as exc:
        status = "missing" if is_missing(exc) else "error"
        record = empty_record(top, left, status, f"{type(exc).__name__}: {exc}")
        record["seconds"] = round(time.time() - started, 1)
        return record

    if hansen.shape != (SAMPLE, SAMPLE) or jrc.shape != (SAMPLE, SAMPLE):
        record = empty_record(top, left, "error", f"unexpected shape {hansen.shape} {jrc.shape}")
        record["seconds"] = round(time.time() - started, 1)
        return record

    both = (hansen > 20) & (jrc == 1)
    shares = block_mean(both)
    west, south, east, north = bounds
    cell_w = (east - west) / CELLS_PER_SIDE
    cell_h = (north - south) / CELLS_PER_SIDE
    cells = []
    ys, xs = np.nonzero(shares > 0)
    for i, j in zip(ys.tolist(), xs.tolist()):
        lon = west + (j + 0.5) * cell_w
        lat = north - (i + 0.5) * cell_h
        cells.append([round(lon, 4), round(lat, 4), round(float(shares[i, j]), 4)])
    cells.sort(key=lambda item: item[2], reverse=True)
    return {
        "tile": name,
        "bounds": [round(float(v), 4) for v in bounds],
        "jrc_bounds": [round(float(v), 4) for v in jrc_bounds],
        "status": "ok",
        "n_cells": int(shares.size),
        "n_loss_cells": len(cells),
        "loss_share": round(float(both.mean()), 6),
        "cells": cells,
        "grid_deg": CELL_DEG,
        "sample_deg": SAMPLE_DEG,
        "method": METHOD,
        "seconds": round(time.time() - started, 1),
    }


def load_done(path):
    done = set()
    records = []
    if not path.exists():
        return done, records
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            tile = record.get("tile")
            if tile:
                done.add(tile)
            records.append(record)
    return done, records


def paint(grid, record):
    if record.get("status") != "ok":
        return
    for lon, lat, share in record.get("cells") or []:
        row = int(round((LAT0 - lat) / CELL_DEG - 0.5))
        col = int(round((lon - LON0) / CELL_DEG - 0.5))
        if 0 <= row < GRID_ROWS and 0 <= col < GRID_COLS:
            grid[row, col] = max(grid[row, col], share)


def write_overview(grid):
    scale = np.sqrt(np.clip(grid / 0.25, 0, 1))
    img = np.empty((GRID_ROWS, GRID_COLS, 3), dtype=np.uint8)
    img[:] = (8, 12, 18)
    hot = grid > 0
    img[hot, 0] = (48 + 207 * scale[hot]).astype(np.uint8)
    img[hot, 1] = (18 + 200 * scale[hot]).astype(np.uint8)
    img[hot, 2] = (16 + 40 * scale[hot]).astype(np.uint8)
    Image.fromarray(img).save(PNG_PATH)


def top_cells(records, limit=25):
    heap = []
    for record in records:
        if record.get("status") != "ok":
            continue
        tile = record["tile"]
        for lon, lat, share in record.get("cells") or []:
            item = (share, lon, lat, tile)
            if len(heap) < limit:
                heapq.heappush(heap, item)
            elif share > heap[0][0]:
                heapq.heapreplace(heap, item)
    ranked = sorted(heap, reverse=True)
    return [
        {"tile": tile, "lon": lon, "lat": lat, "share": share}
        for share, lon, lat, tile in ranked
    ]


def write_summary(records):
    done = sum(1 for record in records if record.get("status") == "ok")
    missing = sum(1 for record in records if record.get("status") == "missing")
    errors = sum(1 for record in records if record.get("status") == "error")
    summary = {
        "tiles_done": done,
        "tiles_failed": missing + errors,
        "tiles_missing": missing,
        "tiles_error": errors,
        "grid_deg": CELL_DEG,
        "sample_deg": SAMPLE_DEG,
        "method": METHOD,
        "cell_threshold": CELL_THRESHOLD,
        "top_cells": top_cells(records),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    tmp = SUMMARY_PATH.with_suffix(".json.tmp")
    with tmp.open("w") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    tmp.replace(SUMMARY_PATH)
    return summary


def log(message):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} {message}", flush=True)


def append_and_publish(record, records, grid, jsonl):
    jsonl.write(json.dumps(record, separators=(",", ":")) + "\n")
    jsonl.flush()
    records.append(record)
    paint(grid, record)
    write_overview(grid)
    summary = write_summary(records)
    if record["status"] == "ok":
        log(
            f"{record['tile']} ok loss_share={record['loss_share']} "
            f"n_loss_cells={record['n_loss_cells']} seconds={record.get('seconds')} "
            f"done={summary['tiles_done']} failed={summary['tiles_failed']}"
        )
    else:
        log(
            f"{record['tile']} {record['status']} {record.get('error', '')} "
            f"seconds={record.get('seconds')} done={summary['tiles_done']} failed={summary['tiles_failed']}"
        )


def main():
    parser = argparse.ArgumentParser(description="Coarse global 2020-forest loss index")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--tile", help="Single Hansen tile name, e.g. 00N_110E")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be >= 1")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_PATH.open("a")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another forest_loss_index.py is already running", file=sys.stderr)
        return 1

    done, records = load_done(JSONL_PATH)
    grid = np.zeros((GRID_ROWS, GRID_COLS), dtype=np.float32)
    for record in records:
        paint(grid, record)

    if args.tile:
        tasks = [parse_hansen_tile(args.tile)]
        tasks = [task for task in tasks if hansen_name(*task) not in done]
    else:
        tasks = [task for task in tile_tasks() if hansen_name(*task) not in done]

    log(
        f"method={METHOD} queued={len(tasks)} already_done={len(done)} workers={args.workers}"
    )
    if not tasks:
        write_overview(grid)
        write_summary(records)
        log("nothing to do")
        return 0

    jsonl = JSONL_PATH.open("a")
    try:
        if len(tasks) == 1:
            record = process_tile(*tasks[0])
            append_and_publish(record, records, grid, jsonl)
        else:
            workers = min(args.workers, len(tasks))
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(process_tile, top, left) for top, left in tasks]
                for future in as_completed(futures):
                    try:
                        record = future.result()
                    except Exception:
                        log(traceback.format_exc())
                        continue
                    append_and_publish(record, records, grid, jsonl)
    finally:
        jsonl.close()
    log("finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
