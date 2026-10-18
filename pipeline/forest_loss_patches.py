"""Native-resolution clearings: JRC GFC2020 forest that Hansen lost after 2020.

A pixel is kept when it is forest on the JRC GFC2020 map (value 1) and Hansen
GFC-2024-v1.12 lossyear is greater than 20 (loss in 2021-2024). The test is
evaluated on the Hansen grid (~30 m). JRC is finer (~10 m) and is read only
for the same geographic window, then aggregated onto those Hansen pixels.

Each window is polygonized. Components under 10 hectares are dropped. Features
are GeoJSON in EPSG:4326, appended to forest_loss_patches.jsonl so a rerun
skips windows already finished. A full 10° tile is never loaded.

Usage:
    python pipeline/forest_loss_patches.py --tile 00N_110E --window 110.05,-0.81,110.11,-0.75
    python pipeline/forest_loss_patches.py --tile 00N_110E
    python pipeline/forest_loss_patches.py --tile-index 0
"""
import os

os.environ.setdefault("GDAL_HTTP_TIMEOUT", "90")
os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "20")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "4")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
os.environ.setdefault("CPL_VSIL_CURL_CHUNK_SIZE", "8388608")

import argparse
import fcntl
import json
import math
import time
from pathlib import Path

import numpy as np
import rasterio
import shapely
from rasterio.enums import Resampling
from rasterio.features import shapes
from rasterio.warp import reproject
from rasterio.windows import Window
from shapely.affinity import scale, translate
from shapely.geometry import mapping, shape

HANSEN_VERSION = "GFC-2024-v1.12"
LOSS_AFTER = 20  # lossyear values 21-24 are calendar years 2021-2024
MIN_HA = 10.0
# Both GeoTIFFs are striped one full-width row at a time (not tiled). A column
# window would re-download those rows, so a tile is read as full-width row bands.
BAND_ROWS = 400
REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "data" / "global"


def hansen_tile_name(top, left):
    ns = f"{abs(top):02d}{'N' if top >= 0 else 'S'}"
    ew = f"{abs(left):03d}{'E' if left >= 0 else 'W'}"
    return f"{ns}_{ew}"


def all_tiles():
    tiles = []
    for top in range(80, -51, -10):
        for left in range(-180, 180, 10):
            tiles.append(hansen_tile_name(top, left))
    return tiles


def parse_tile(tile):
    ns, ew = tile.split("_")
    top = int(ns[:-1]) * (1 if ns[-1] == "N" else -1)
    left = int(ew[:-1]) * (1 if ew[-1] == "E" else -1)
    return top, left


def hansen_url(tile):
    return (
        "/vsicurl/https://storage.googleapis.com/earthenginepartners-hansen/"
        f"{HANSEN_VERSION}/Hansen_{HANSEN_VERSION}_lossyear_{tile}.tif"
    )


def jrc_url_for_tile(tile):
    top, left = parse_tile(tile)
    ns = f"{'N' if top >= 0 else 'S'}{abs(top)}"
    ew = f"{'E' if left >= 0 else 'W'}{abs(left)}"
    name = f"JRC_GFC2020_V4_{ns}_{ew}.tif"
    return (
        "/vsicurl/https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/FOREST/"
        f"GFC2020/LATEST/tiles/{name}"
    )


def parse_bbox(text):
    parts = [float(p) for p in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("window must be minLon,minLat,maxLon,maxLat")
    min_lon, min_lat, max_lon, max_lat = parts
    if not (min_lon < max_lon and min_lat < max_lat):
        raise argparse.ArgumentTypeError("window min must be less than max")
    return min_lon, min_lat, max_lon, max_lat


def cover_window(src, bbox):
    """Pixel window that covers bbox, clipped to the dataset. Never the whole tile unless asked."""
    raw = rasterio.windows.from_bounds(*bbox, transform=src.transform)
    col0 = max(0, math.floor(raw.col_off))
    row0 = max(0, math.floor(raw.row_off))
    col1 = min(src.width, math.ceil(raw.col_off + raw.width))
    row1 = min(src.height, math.ceil(raw.row_off + raw.height))
    if col1 <= col0 or row1 <= row0:
        return None
    return Window(col0, row0, col1 - col0, row1 - row0)


def iter_blocks(window):
    """Full-width row bands. Column splits would reread the same TIFF strips."""
    col0 = int(window.col_off)
    row0 = int(window.row_off)
    width = int(window.width)
    for row in range(row0, row0 + int(window.height), BAND_ROWS):
        height = min(BAND_ROWS, row0 + int(window.height) - row)
        yield Window(col0, row, width, height)


def is_missing(exc):
    message = str(exc).lower()
    return any(token in message for token in ("404", "not found", "no such file", "does not exist", "400"))


def area_ha(geom):
    centroid = geom.centroid
    kx = 111_320.0 * math.cos(math.radians(centroid.y))
    ky = 110_574.0
    shifted = translate(geom, xoff=-centroid.x, yoff=-centroid.y)
    meters = scale(shifted, xfact=kx, yfact=ky, origin=(0, 0))
    return abs(meters.area) / 10_000.0


def round_geom(geom):
    def _round(coords):
        return np.round(np.asarray(coords, dtype=np.float64), 6)

    return shapely.transform(geom, _round)


def forest_fraction(jrc, bounds, dst_shape, dst_transform, dst_crs):
    """Share of each Hansen pixel that is JRC forest (value 1). Reads only this window."""
    raw = rasterio.windows.from_bounds(*bounds, transform=jrc.transform)
    pad = 2
    col0 = max(0, math.floor(raw.col_off) - pad)
    row0 = max(0, math.floor(raw.row_off) - pad)
    col1 = min(jrc.width, math.ceil(raw.col_off + raw.width) + pad)
    row1 = min(jrc.height, math.ceil(raw.row_off + raw.height) + pad)
    dest = np.zeros(dst_shape, dtype=np.float32)
    if col1 <= col0 or row1 <= row0:
        return dest
    window = Window(col0, row0, col1 - col0, row1 - row0)
    values = jrc.read(1, window=window)
    forest = (values == 1).astype(np.float32)
    reproject(
        source=forest,
        destination=dest,
        src_transform=jrc.window_transform(window),
        src_crs=jrc.crs or "EPSG:4326",
        dst_transform=dst_transform,
        dst_crs=dst_crs or "EPSG:4326",
        resampling=Resampling.average,
    )
    return dest


def polygons_for_block(loss, forest_frac, transform, tile):
    overlap = (loss > LOSS_AFTER) & (forest_frac >= 0.5)
    share = float(overlap.mean()) if overlap.size else 0.0
    features = []
    if not overlap.any():
        return features, share
    for geom_map, value in shapes(overlap.astype(np.uint8), mask=overlap, transform=transform):
        if value != 1:
            continue
        geom = shape(geom_map)
        if geom.is_empty:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)
        if geom.is_empty:
            continue
        hectares = area_ha(geom)
        if hectares < MIN_HA:
            continue
        centroid = geom.centroid
        rounded = round_geom(geom)
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(rounded),
                "properties": {
                    "area_ha": round(hectares, 2),
                    "centroid_lon": round(float(centroid.x), 6),
                    "centroid_lat": round(float(centroid.y), 6),
                    "hansen_tile": tile,
                },
            }
        )
    return features, share


def load_json(path, default):
    if not path.exists():
        return default
    with open(path) as f:
        raw = f.read().strip()
    if not raw:
        return default
    return json.loads(raw)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def ensure_status(path):
    if not path.exists():
        save_json(path, {"finished": [], "failed": {}, "skipped": []})


def update_status(path, tile, state, error=None):
    """state is finished, failed, or skipped. One tile is in only one list."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        status = load_json(path, {"finished": [], "failed": {}, "skipped": []})
        for key in ("finished", "skipped"):
            status[key] = [name for name in status.get(key, []) if name != tile]
        failed = status.get("failed") or {}
        failed.pop(tile, None)
        if state == "finished":
            status["finished"].append(tile)
        elif state == "skipped":
            status["skipped"].append(tile)
        elif state == "failed":
            failed[tile] = error or "failed"
        status["failed"] = failed
        status["finished"] = sorted(set(status["finished"]))
        status["skipped"] = sorted(set(status["skipped"]))
        save_json(path, status)
        fcntl.flock(lock, fcntl.LOCK_UN)


def append_features(paths, features):
    if not features:
        return
    payload = "".join(json.dumps(feature, separators=(",", ":")) + "\n" for feature in features)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(payload)
            f.flush()
            fcntl.flock(f, fcntl.LOCK_UN)


def block_key(window):
    return f"{int(window.row_off)}_{int(window.col_off)}_{int(window.height)}_{int(window.width)}"


def process_tile(tile, bbox, out_dir):
    out_dir = Path(out_dir)
    status_path = out_dir / "forest_loss_patches_status.json"
    ensure_status(status_path)
    progress_path = out_dir / "progress" / f"{tile}.json"
    jsonl_paths = [
        out_dir / "forest_loss_patches.jsonl",
        out_dir / "chunks" / f"{tile}.jsonl",
    ]
    done = set(load_json(progress_path, []))

    if bbox is None:
        status = load_json(status_path, {"finished": [], "failed": {}, "skipped": []})
        if tile in status.get("finished", []) or tile in status.get("skipped", []):
            print(f"{tile} already {('finished' if tile in status.get('finished', []) else 'skipped')}, nothing to do")
            return 0

    t0 = time.time()
    try:
        hansen_ds = rasterio.open(hansen_url(tile))
    except Exception as exc:
        message = str(exc)
        missing = is_missing(exc)
        # Ocean tiles are absent from the bucket. Treat an open failure as skipped.
        print(f"{tile} hansen open failed ({'skip' if missing else 'fail'}): {message}")
        if bbox is None:
            update_status(status_path, tile, "skipped" if missing else "failed", message)
        return 0 if missing else 1

    try:
        jrc_ds = rasterio.open(jrc_url_for_tile(tile))
    except Exception as exc:
        hansen_ds.close()
        message = str(exc)
        print(f"{tile} jrc open failed: {message}")
        if bbox is None:
            update_status(status_path, tile, "failed", message)
        return 1

    try:
        if bbox is None:
            target = Window(0, 0, hansen_ds.width, hansen_ds.height)
        else:
            target = cover_window(hansen_ds, bbox)
            if target is None:
                print(f"{tile} window {bbox} does not intersect the tile")
                return 1

        res_x = abs(hansen_ds.transform.a)
        res_y = abs(hansen_ds.transform.e)
        print(
            f"{tile} hansen {hansen_ds.width}x{hansen_ds.height} "
            f"res_deg=({res_x:.6f},{res_y:.6f}) "
            f"jrc {jrc_ds.width}x{jrc_ds.height} "
            f"res_deg=({abs(jrc_ds.transform.a):.6f},{abs(jrc_ds.transform.e):.6f}) "
            f"blocks={sum(1 for _ in iter_blocks(target))}"
        )

        total_features = 0
        samples = []
        for block in iter_blocks(target):
            key = block_key(block)
            if key in done:
                print(f"{tile} block {key} already done", flush=True)
                continue
            bounds = rasterio.windows.bounds(block, hansen_ds.transform)
            transform = hansen_ds.window_transform(block)
            features = []
            share = 0.0
            for attempt in range(3):
                try:
                    loss = hansen_ds.read(1, window=block)
                    if not (loss > LOSS_AFTER).any():
                        features, share = [], 0.0
                    else:
                        fraction = forest_fraction(jrc_ds, bounds, loss.shape, transform, hansen_ds.crs)
                        features, share = polygons_for_block(loss, fraction, transform, tile)
                    break
                except Exception as exc:
                    if attempt == 2 or is_missing(exc):
                        raise
                    print(f"{tile} block {key} retry {attempt + 1}: {exc}", flush=True)
                    time.sleep(2 * (attempt + 1))
            append_features(jsonl_paths, features)
            done.add(key)
            save_json(progress_path, sorted(done))
            total_features += len(features)
            if features and len(samples) < 3:
                samples.extend(features[: 3 - len(samples)])
            print(
                f"{tile} block {key} overlap_share={share:.4f} "
                f"polygons={len(features)} bbox={bounds[0]:.4f},{bounds[1]:.4f},{bounds[2]:.4f},{bounds[3]:.4f}",
                flush=True,
            )
    except Exception as exc:
        message = str(exc)
        print(f"{tile} failed: {message}")
        if bbox is None:
            update_status(status_path, tile, "failed", message)
        return 1
    finally:
        hansen_ds.close()
        jrc_ds.close()

    if bbox is None:
        update_status(status_path, tile, "finished")
    elapsed = time.time() - t0
    print(f"{tile} wrote {total_features} polygons in {elapsed:.1f}s")
    if samples:
        props = samples[0]["properties"]
        print(
            f"sample centroid {props['centroid_lon']},{props['centroid_lat']} "
            f"area_ha={props['area_ha']}"
        )
    return 0


def main():
    parser = argparse.ArgumentParser(description="Native-resolution post-2020 forest-loss polygons")
    parser.add_argument("--tile", help="Hansen 10° tile name, e.g. 00N_110E")
    parser.add_argument("--window", type=parse_bbox, help="minLon,minLat,maxLon,maxLat inside the tile")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="directory for jsonl, chunks, and status")
    parser.add_argument("--tile-index", type=int, help="print the tile name for this index and exit")
    args = parser.parse_args()
    if args.tile_index is not None:
        tiles = all_tiles()
        if not 0 <= args.tile_index < len(tiles):
            raise SystemExit(f"tile index {args.tile_index} outside 0..{len(tiles) - 1}")
        print(tiles[args.tile_index])
        return
    if not args.tile:
        parser.error("--tile is required")
    parse_tile(args.tile)
    code = process_tile(args.tile, args.window, args.out)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
