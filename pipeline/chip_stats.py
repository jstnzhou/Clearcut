"""Per-chip Hansen loss (2021-2024) and JRC GFC 2020 forest shares.

Usage:
    python pipeline/chip_stats.py data/plots/kalbar1

Reads the Hansen lossyear window for the full plot bbox once, then slices it
into a 2x2 grid in raster pixel space (row 0 is north). Writes chip_stats.json.
"""
import json
import math
import os
import sys

os.environ.setdefault("GDAL_HTTP_TIMEOUT", "90")
os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "20")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")

import numpy as np
import rasterio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify import hansen_window

def jrc_tile_url(lat, lon):
    # 10° tiles named by the top-left corner. The published file for 0N, 110E is N0_E110.
    top = int(math.ceil(lat / 10.0) * 10)
    left = int(math.floor(lon / 10.0) * 10)
    ns = f"{'N' if top >= 0 else 'S'}{abs(top)}"
    ew = f"{'E' if left >= 0 else 'W'}{abs(left)}"
    name = f"JRC_GFC2020_V4_{ns}_{ew}.tif"
    return f"https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/FOREST/GFC2020/LATEST/tiles/{name}"

def chip_slices(h, w):
    """NW, NE, SW, SE as (row0, row1, col0, col1). Row 0 is north."""
    rh, cw = h // 2, w // 2
    return [
        (0, rh, 0, cw),
        (0, rh, cw, w),
        (rh, h, 0, cw),
        (rh, h, cw, w),
    ]


def polygons(bbox):
    min_lon, min_lat, max_lon, max_lat = bbox
    mid_lon = (min_lon + max_lon) / 2
    mid_lat = (min_lat + max_lat) / 2
    rings = [
        [[min_lon, max_lat], [mid_lon, max_lat], [mid_lon, mid_lat], [min_lon, mid_lat], [min_lon, max_lat]],
        [[mid_lon, max_lat], [max_lon, max_lat], [max_lon, mid_lat], [mid_lon, mid_lat], [mid_lon, max_lat]],
        [[min_lon, mid_lat], [mid_lon, mid_lat], [mid_lon, min_lat], [min_lon, min_lat], [min_lon, mid_lat]],
        [[mid_lon, mid_lat], [max_lon, mid_lat], [max_lon, min_lat], [mid_lon, min_lat], [mid_lon, mid_lat]],
    ]
    return rings


def share(mask):
    return round(float(mask.mean()) if mask.size else 0.0, 3)


def read_jrc(bbox, lat, lon):
    url = jrc_tile_url(lat, lon)
    try:
        with rasterio.open(f"/vsicurl/{url}") as src:
            window = rasterio.windows.from_bounds(*bbox, transform=src.transform)
            a = src.read(1, window=window)
        return url, a, None
    except Exception as exc:
        return None, None, f"{url}: {exc}"


def main():
    plot = sys.argv[1] if len(sys.argv) > 1 else "data/plots/kalbar1"
    evidence = json.load(open(os.path.join(plot, "evidence.json")))
    bbox = evidence["bbox"]
    loss = hansen_window(bbox, evidence["lat"], evidence["lon"])
    jrc_url, forest, jrc_error = read_jrc(bbox, evidence["lat"], evidence["lon"])
    loss_slices = chip_slices(*loss.shape)
    forest_slices = chip_slices(*forest.shape) if forest is not None else None

    chips = []
    for i, (r0, r1, c0, c1) in enumerate(loss_slices):
        jrc_share = None
        if forest_slices is not None:
            fr0, fr1, fc0, fc1 = forest_slices[i]
            jrc_share = share(forest[fr0:fr1, fc0:fc1] == 1)
        chips.append(
            {
                "id": i,
                "hansen_loss_share": share(loss[r0:r1, c0:c1] > 20),
                "jrc_forest_share": jrc_share,
                "polygon": polygons(bbox)[i],
            }
        )

    out = {"bbox": bbox, "jrc_source": jrc_url, "chips": chips}
    if jrc_error:
        out["jrc_error"] = jrc_error
    path = os.path.join(plot, "chip_stats.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
