"""Fetch evidence for one plot: Hansen forest-loss years + Sentinel-2 before/after imagery.

Usage:
    python pipeline/fetch_plot.py --lat -0.78 --lon 110.08 --radius 0.03 --out data/plots/kalbar1

Writes before.png, after.png, before_after.png and evidence.json to --out.
"""
import argparse
import json
import math
import os

import numpy as np
import planetary_computer as pc
import pystac_client
import rasterio
from PIL import Image
from rasterio.warp import transform_bounds
from shapely.geometry import box, shape

HANSEN_VERSION = "GFC-2024-v1.12"  # loss years 2001-2024
STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
EUDR_CUTOFF_YEAR = 2020  # EUDR: deforestation after 2020-12-31 is non-compliant


def hansen_tile(lat, lon):
    # Tiles are 10x10 degrees, named by their top-left corner, e.g. 00N_110E.
    top = math.ceil(lat / 10) * 10
    left = math.floor(lon / 10) * 10
    ns = f"{abs(top):02d}{'N' if top >= 0 else 'S'}"
    ew = f"{abs(left):03d}{'E' if left >= 0 else 'W'}"
    return f"{ns}_{ew}"


def hansen_loss_years(bbox, lat, lon):
    url = (
        f"/vsicurl/https://storage.googleapis.com/earthenginepartners-hansen/"
        f"{HANSEN_VERSION}/Hansen_{HANSEN_VERSION}_lossyear_{hansen_tile(lat, lon)}.tif"
    )
    with rasterio.open(url) as src:
        a = src.read(1, window=rasterio.windows.from_bounds(*bbox, transform=src.transform))
    years, counts = np.unique(a[a > 0], return_counts=True)
    total = a.size
    by_year = {2000 + int(y): round(float(c) / total, 4) for y, c in zip(years, counts)}
    post = sum(v for y, v in by_year.items() if y > EUDR_CUTOFF_YEAR)
    return {"loss_share_by_year": by_year, "loss_share_post_cutoff": round(post, 4)}


def best_scene(catalog, bbox, daterange, max_cloud=40):
    aoi = box(*bbox)
    items = [
        i
        for i in catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=bbox,
            datetime=daterange,
            query={"eo:cloud_cover": {"lt": max_cloud}},
        ).items()
        if shape(i.geometry).contains(aoi)
    ]
    if not items:
        return None
    return min(items, key=lambda i: i.properties["eo:cloud_cover"])


def read_reflectance(item, band, bbox):
    with rasterio.open(item.assets[band].href) as src:
        b = transform_bounds("EPSG:4326", src.crs, *bbox)
        a = src.read(1, window=rasterio.windows.from_bounds(*b, transform=src.transform)).astype(float)
    # Processing baseline >= 04.00 (scenes from 2022 on) adds +1000 to every band.
    offset = 1000 if float(item.properties.get("s2:processing_baseline", "0")) >= 4.0 else 0
    return np.clip(a - offset, 0, None) / 10000


def scene_evidence(item, bbox):
    r, g, b, n = (read_reflectance(item, k, bbox) for k in ["B04", "B03", "B02", "B08"])
    ndvi = (n - r) / (n + r + 1e-6)
    rgb = np.clip(np.stack([r, g, b], -1) / 0.15 * 255, 0, 255).astype(np.uint8)
    stats = {
        "date": str(item.datetime.date()),
        "scene_id": item.id,
        "cloud_cover": item.properties["eo:cloud_cover"],
        "ndvi_mean": round(float(ndvi.mean()), 3),
    }
    return rgb, stats


def fetch_year(catalog, bbox, year):
    rng = f"{year}-01-01/{year}-12-31"
    return best_scene(catalog, bbox, rng, max_cloud=40) or best_scene(catalog, bbox, rng, max_cloud=80)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    p.add_argument("--radius", type=float, default=0.03, help="half-width in degrees")
    p.add_argument("--bbox", help="minLon,minLat,maxLon,maxLat. Overrides lat/lon/radius.")
    p.add_argument("--before", default="2019-01-01/2020-12-31")
    p.add_argument("--after", default="2025-01-01/2026-10-01")
    p.add_argument("--years", action="store_true", help="Save one clear scene per year from 2019 through 2026.")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    if args.bbox:
        bbox = [float(v) for v in args.bbox.split(",")]
        lat = (bbox[1] + bbox[3]) / 2
        lon = (bbox[0] + bbox[2]) / 2
    else:
        if args.lat is None or args.lon is None:
            p.error("pass --lat and --lon, or --bbox")
        lat, lon = args.lat, args.lon
        bbox = [lon - args.radius, lat - args.radius, lon + args.radius, lat + args.radius]
    os.makedirs(args.out, exist_ok=True)
    catalog = pystac_client.Client.open(STAC_URL, modifier=pc.sign_inplace)

    evidence = {"lat": lat, "lon": lon, "bbox": bbox, "hansen": hansen_loss_years(bbox, lat, lon)}
    images = []
    if args.years:
        scenes = []
        target = None
        for year in range(2019, 2027):
            item = fetch_year(catalog, bbox, year)
            if item is None:
                continue
            rgb, stats = scene_evidence(item, bbox)
            if target is None:
                target = rgb.shape[:2]
            elif rgb.shape[:2] != target:
                rgb = np.array(Image.fromarray(rgb).resize((target[1], target[0]), Image.Resampling.BILINEAR))
            name = f"scene_{year}.png"
            Image.fromarray(rgb).save(os.path.join(args.out, name))
            stats["year"] = year
            stats["file"] = name
            scenes.append(stats)
            print(f"{year} {stats['date']} cloud {stats['cloud_cover']}", flush=True)
        if len(scenes) < 2:
            raise SystemExit("fewer than two clear scenes")
        evidence["scenes"] = scenes
        evidence["before"] = {k: scenes[0][k] for k in ("date", "scene_id", "cloud_cover", "ndvi_mean")}
        evidence["after"] = {k: scenes[-1][k] for k in ("date", "scene_id", "cloud_cover", "ndvi_mean")}
        Image.open(os.path.join(args.out, scenes[0]["file"])).save(os.path.join(args.out, "before.png"))
        Image.open(os.path.join(args.out, scenes[-1]["file"])).save(os.path.join(args.out, "after.png"))
        images = [np.array(Image.open(os.path.join(args.out, "before.png"))), np.array(Image.open(os.path.join(args.out, "after.png")))]
    else:
        for name, rng in [("before", args.before), ("after", args.after)]:
            item = best_scene(catalog, bbox, rng)
            if item is None:
                evidence[name] = None
                continue
            rgb, stats = scene_evidence(item, bbox)
            Image.fromarray(rgb).save(os.path.join(args.out, f"{name}.png"))
            evidence[name] = stats
            images.append(rgb)

    if len(images) == 2 and images[0].shape == images[1].shape:
        gap = np.full((images[0].shape[0], 10, 3), 255, np.uint8)
        Image.fromarray(np.concatenate([images[0], gap, images[1]], 1)).save(
            os.path.join(args.out, "before_after.png")
        )

    with open(os.path.join(args.out, "evidence.json"), "w") as f:
        json.dump(evidence, f, indent=2)
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
