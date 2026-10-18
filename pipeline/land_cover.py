"""Third dataset: what land cover replaced post-cutoff forest loss.

Uses Impact Observatory Annual LULC (10 m on Planetary Computer) masked to
Hansen post-2020 loss pixels. ESA WorldCover 2020/2021 is stored as context
(oil palm often still reads as tree cover there).

Usage:
    python pipeline/land_cover.py data/plots/kalbar1
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import numpy as np
import planetary_computer as pc
import pystac_client
import rasterio
from rasterio.warp import reproject, transform_bounds, Resampling

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
HANSEN_VERSION = "GFC-2024-v1.12"
EUDR_CUTOFF_YEAR = 2020

WORLDCOVER = {
    10: "Tree cover",
    20: "Shrubland",
    30: "Grassland",
    40: "Cropland",
    50: "Built-up",
    60: "Bare / sparse",
    70: "Snow/ice",
    80: "Water",
    90: "Wetland",
    95: "Mangrove",
    100: "Moss/lichen",
}

IO_LULC = {
    1: "Water",
    2: "Trees",
    4: "Flooded vegetation",
    5: "Crops",
    7: "Built area",
    8: "Bare ground",
    9: "Snow/ice",
    10: "Clouds",
    11: "Rangeland",
}


def hansen_tile(lat: float, lon: float) -> str:
    top = math.ceil(lat / 10) * 10
    left = math.floor(lon / 10) * 10
    ns = f"{abs(top):02d}{'N' if top >= 0 else 'S'}"
    ew = f"{abs(left):03d}{'E' if left >= 0 else 'W'}"
    return f"{ns}_{ew}"


def catalog():
    return pystac_client.Client.open(STAC, modifier=pc.sign_inplace)


def read_hansen_loss(bbox, lat, lon):
    url = (
        f"/vsicurl/https://storage.googleapis.com/earthenginepartners-hansen/"
        f"{HANSEN_VERSION}/Hansen_{HANSEN_VERSION}_lossyear_{hansen_tile(lat, lon)}.tif"
    )
    with rasterio.open(url) as src:
        window = rasterio.windows.from_bounds(*bbox, transform=src.transform)
        data = src.read(1, window=window)
        transform = src.window_transform(window)
    return data, transform


def histogram(arr: np.ndarray, legend: dict) -> dict:
    if arr.size == 0:
        return {}
    vals, counts = np.unique(arr, return_counts=True)
    total = int(counts.sum()) or 1
    out = {}
    for value, count in zip(vals, counts):
        key = legend.get(int(value), f"class_{int(value)}")
        out[key] = round(float(count) / total, 4)
    return dict(sorted(out.items(), key=lambda item: -item[1]))


def resample_to(src_item, asset: str, shape, transform, crs="EPSG:4326"):
    signed = pc.sign(src_item)
    dst = np.zeros(shape, dtype=np.uint8)
    with rasterio.open(signed.assets[asset].href) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs=crs,
            resampling=Resampling.nearest,
        )
    return dst


def pick_io_item(cat, bbox, year: int):
    # Datetime filters on this collection can return adjacent years; pin by item id.
    items = [
        it
        for it in cat.search(
            collections=["io-lulc-annual-v02"],
            bbox=bbox,
            datetime=f"{year}-01-01/{year}-12-31",
            max_items=30,
        ).items()
        if it.id.endswith(f"-{year}")
    ]
    if not items:
        items = [
            it
            for it in cat.search(
                collections=["io-lulc-annual-v02"],
                bbox=bbox,
                max_items=40,
            ).items()
            if it.id.endswith(f"-{year}")
        ]
    if not items:
        return None
    # Prefer the 49M / local MGRS-style tile when several overlap.
    ranked = sorted(
        items,
        key=lambda it: (0 if "49M" in it.id or "49N" in it.id else 1, it.id),
    )
    return ranked[0]


def pick_worldcover(cat, bbox, year: int):
    for item in cat.search(collections=["esa-worldcover"], bbox=bbox, max_items=8).items():
        if str(year) in item.id:
            return item
    return None


def dominant(shares: dict, n=3):
    return [{"class": k, "share": v} for k, v in list(shares.items())[:n]]


def summarize_replacement(shares: dict) -> str:
    if not shares:
        return "No land-cover sample on post-cutoff loss pixels."
    top = list(shares.items())[:3]
    parts = [f"{name} ({share:.0%})" for name, share in top]
    lead = top[0][0]
    note = (
        "Impact Observatory Annual LULC (10 m) on Hansen post-2020 loss pixels. "
        "Oil palm and young plantation often register as rangeland or trees, not a named crop."
    )
    return f"On cleared ground, land cover is mostly {', '.join(parts)}. {note}"


def build_land_cover(plot_dir: Path) -> dict:
    evidence = json.loads((plot_dir / "evidence.json").read_text())
    bbox = evidence["bbox"]
    lat = evidence["lat"]
    lon = evidence["lon"]
    loss, transform = read_hansen_loss(bbox, lat, lon)
    # Hansen lossyear band: 1 = 2001 … 24 = 2024
    post = (loss > (EUDR_CUTOFF_YEAR - 2000)) & (loss > 0)
    shape = loss.shape
    cat = catalog()

    worldcover = {}
    for year in (2020, 2021):
        item = pick_worldcover(cat, bbox, year)
        if not item:
            continue
        arr = resample_to(item, "map", shape, transform)
        worldcover[str(year)] = {
            "item_id": item.id,
            "plot_share": histogram(arr[arr > 0], WORLDCOVER),
            "on_post_cutoff_loss": histogram(arr[post], WORLDCOVER),
        }

    io = {}
    for year in (2020, 2021, 2022, 2023):
        item = pick_io_item(cat, bbox, year)
        if not item:
            continue
        arr = resample_to(item, "data", shape, transform)
        io[str(year)] = {
            "item_id": item.id,
            "plot_share": histogram(arr[arr > 0], IO_LULC),
            "on_post_cutoff_loss": histogram(arr[post], IO_LULC),
        }

    latest_year = max(io.keys()) if io else None
    replacement = io[latest_year]["on_post_cutoff_loss"] if latest_year else {}
    baseline_year = "2020" if "2020" in io else None
    baseline_on_loss = (
        io[baseline_year]["on_post_cutoff_loss"] if baseline_year else {}
    )

    result = {
        "dataset": "Impact Observatory Annual LULC v02 + ESA WorldCover",
        "source": "Microsoft Planetary Computer STAC",
        "resolution_m": 10,
        "hansen_version": HANSEN_VERSION,
        "post_cutoff_loss_pixels": int(post.sum()),
        "post_cutoff_loss_share": round(float(post.mean()), 4),
        "replacement_year": int(latest_year) if latest_year else None,
        "replacement_on_loss": replacement,
        "replacement_top": dominant(replacement),
        "baseline_year": int(baseline_year) if baseline_year else None,
        "baseline_on_loss_pixels": baseline_on_loss,
        "summary": summarize_replacement(replacement),
        "io_lulc_annual": io,
        "esa_worldcover": worldcover,
        "caveat": (
            "WorldCover often still labels mature plantation as tree cover. "
            "IO LULC 'Crops' / 'Rangeland' on loss pixels is the clearer replacement signal; "
            "it is land cover, not a crop-species map (oil palm vs soy vs rubber)."
        ),
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("plot")
    args = parser.parse_args()
    plot_dir = Path(args.plot)
    result = build_land_cover(plot_dir)
    out = plot_dir / "land_cover.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "wrote": str(out),
        "replacement_year": result["replacement_year"],
        "replacement_top": result["replacement_top"],
        "summary": result["summary"],
    }, indent=2))


if __name__ == "__main__":
    main()
