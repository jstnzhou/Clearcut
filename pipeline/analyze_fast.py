"""Fast rectangle check: parallel yearly RGB, one video per tile, high-reasoning calls together.

Usage:
    python pipeline/analyze_fast.py --bbox minLon,minLat,maxLon,maxLat --out data/plots/name
"""
import os

os.environ.setdefault("GDAL_HTTP_TIMEOUT", "90")
os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "20")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")

import argparse
import json
import math
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import planetary_computer as pc
import pystac_client
import rasterio
from PIL import Image, ImageFilter
from perceptron import Client, video
from rasterio.warp import transform_bounds
from shapely.geometry import box, shape

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
KEY_FILE = os.path.expanduser("~/Desktop/perceptron-key.txt")
YEARS = range(2020, 2027)
PROMPT = (
    "This video is one frame per year of the same land, from 2020 onward, in order, about one frame per second. "
    "Reply with one number from 0.0 to 1.0 and nothing else. "
    "The number is your confidence that forest in the early frames was converted to plantation by the later frames: "
    "rectangular blocks, straight access roads, or planting rows. "
    "0.0 means still forest or no conversion. 1.0 means a clear plantation conversion. "
    "Use a value in between when only part of the tile changed or you are unsure."
)


def api_key():
    if os.environ.get("PERCEPTRON_API_KEY"):
        return os.environ["PERCEPTRON_API_KEY"]
    with open(KEY_FILE) as f:
        return f.readline().strip()


def catalog():
    return pystac_client.Client.open(STAC, modifier=pc.sign_inplace)


def fast_item(bbox, year):
    rng = f"{year}-01-01/{year}-12-31"
    aoi = box(*bbox)
    cat = catalog()
    for max_cloud in (40, 80):
        search = cat.search(
            collections=["sentinel-2-l2a"],
            bbox=bbox,
            datetime=rng,
            query={"eo:cloud_cover": {"lt": max_cloud}},
            sortby="properties.eo:cloud_cover",
            max_items=8,
        )
        items = [i for i in search.items() if shape(i.geometry).contains(aoi)]
        if items:
            return items[0]
    return None


def read_rgb(item, bbox):
    bands = []
    for name in ("B04", "B03", "B02"):
        with rasterio.open(item.assets[name].href) as src:
            window = transform_bounds("EPSG:4326", src.crs, *bbox)
            values = src.read(1, window=rasterio.windows.from_bounds(*window, transform=src.transform)).astype(float)
        offset = 1000 if float(item.properties.get("s2:processing_baseline", "0")) >= 4.0 else 0
        bands.append(np.clip(values - offset, 0, None) / 10000)
    return np.clip(np.stack(bands, -1) / 0.15 * 255, 0, 255).astype(np.uint8)


def fetch_year(bbox, year, out):
    item = fast_item(bbox, year)
    if item is None:
        return None
    rgb = read_rgb(item, bbox)
    stats = {
        "date": str(item.datetime.date()),
        "scene_id": item.id,
        "cloud_cover": item.properties["eo:cloud_cover"],
        "year": year,
        "file": f"scene_{year}.png",
    }
    return rgb, stats


def even_image(im):
    w, h = im.size
    return im.crop((0, 0, max(2, w - w % 2), max(2, h - h % 2)))


def make_video(frames, path):
    folder = path + ".frames"
    os.makedirs(folder, exist_ok=True)
    for i, frame in enumerate(frames):
        even_image(frame).save(os.path.join(folder, f"{i:02d}.png"))
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", "1", "-i", os.path.join(folder, "%02d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", path,
        ],
        check=True,
    )


def parse_score(text):
    import re
    match = re.search(r"\d+(?:\.\d+)?", text or "")
    if not match:
        return None
    value = float(match.group())
    if value > 1:
        value = value / 100 if value <= 100 else 1.0
    return round(max(0.0, min(1.0, value)), 3)


def ask_video(path):
    client = Client(api_key=api_key())
    started = time.perf_counter()
    response = client.chat.completions.create(
        model="perceptron-mk1.5",
        messages=[{"role": "user", "content": [video(path), {"type": "text", "text": PROMPT}]}],
        max_completion_tokens=1024,
        reasoning_effort="high",
    )
    message = response.choices[0].message
    text = message.content or ""
    reasoning = getattr(response, "reasoning", None) or getattr(message, "reasoning_content", None) or ""
    score = parse_score(text)
    if score is None:
        score = parse_score(reasoning)
    return score, text, round(time.perf_counter() - started, 2)


def month_near(date_str, anchor):
    month = int(date_str[5:7])
    return min((month - anchor) % 12, (anchor - month) % 12) <= 1


def keep_season(scenes):
    """Drop years whose scene is more than a month from the latest scene."""
    if len(scenes) < 2:
        return scenes
    anchor = int(scenes[-1][1]["date"][5:7])
    kept = [row for row in scenes if month_near(row[1]["date"], anchor)]
    return kept if len(kept) >= 2 else scenes


def tiles(width, height, target=250, max_tiles=16):
    cols = max(1, math.ceil(width / target))
    rows = max(1, math.ceil(height / target))
    while cols * rows > max_tiles:
        if cols >= rows and cols > 1:
            cols -= 1
        elif rows > 1:
            rows -= 1
        else:
            break
    boxes = []
    for row in range(rows):
        for col in range(cols):
            left = col * width // cols
            right = (col + 1) * width // cols
            top = row * height // rows
            bottom = (row + 1) * height // rows
            boxes.append((left, top, right, bottom, row, col))
    return rows, cols, boxes


def ring(pixel_box, size, bbox):
    left, top, right, bottom = pixel_box
    width, height = size
    min_lon, min_lat, max_lon, max_lat = bbox
    def lon(x):
        return min_lon + (max_lon - min_lon) * (x / width)
    def lat(y):
        return max_lat - (max_lat - min_lat) * (y / height)
    coords = [[lon(left), lat(top)], [lon(right), lat(top)], [lon(right), lat(bottom)], [lon(left), lat(bottom)]]
    coords.append(coords[0])
    return coords


def colorize(scores):
    out = np.zeros(scores.shape + (4,), np.uint8)
    low = scores <= 0.5
    high = ~low
    t = np.zeros(scores.shape, np.float32)
    t[low] = np.divide(scores[low], 0.5, out=np.zeros_like(scores[low]), where=True)
    t[high] = (scores[high] - 0.5) / 0.5
    out[low, 0] = (30 + (183 - 30) * t[low]).astype(np.uint8)
    out[low, 1] = (132 + (149 - 132) * t[low]).astype(np.uint8)
    out[low, 2] = (73 + (11 - 73) * t[low]).astype(np.uint8)
    out[high, 0] = (183 + (192 - 183) * t[high]).astype(np.uint8)
    out[high, 1] = (149 + (57 - 149) * t[high]).astype(np.uint8)
    out[high, 2] = (11 + (43 - 11) * t[high]).astype(np.uint8)
    out[:, :, 3] = (30 + 170 * np.clip(scores, 0, 1)).astype(np.uint8)
    return out


def read_masks(bbox):
    from chip_stats import read_jrc
    from verify import hansen_window

    lat = (bbox[1] + bbox[3]) / 2
    lon = (bbox[0] + bbox[2]) / 2
    try:
        loss = hansen_window(bbox, lat, lon) > 20
    except Exception as exc:
        return None, None, f"hansen: {exc}"
    _, forest, jrc_error = read_jrc(bbox, lat, lon)
    if forest is None:
        return None, None, jrc_error or "jrc missing"
    return loss, forest == 1, None


def resize_mask(mask, width, height):
    image = Image.fromarray(mask.astype(np.uint8) * 255)
    return np.array(image.resize((width, height), Image.Resampling.NEAREST)) > 0


def paint(size, score_of, pixel_mask=None):
    width, height = size
    grid = np.zeros((height, width), np.float32)
    known = np.zeros((height, width), bool)
    for box, score in score_of:
        if score is None:
            continue
        left, top, right, bottom = box
        grid[top:bottom, left:right] = score
        known[top:bottom, left:right] = True
    blurred = np.array(
        Image.fromarray((grid * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(radius=12)),
        dtype=np.float32,
    ) / 255
    blurred[~known] = 0
    if pixel_mask is not None:
        blurred *= pixel_mask
    return colorize(np.clip(blurred, 0, 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bbox", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    bbox = [float(v) for v in args.bbox.split(",")]
    os.makedirs(args.out, exist_ok=True)
    started = time.perf_counter()

    with ThreadPoolExecutor(max_workers=len(list(YEARS))) as pool:
        fetched = list(pool.map(lambda year: fetch_year(bbox, year, args.out), YEARS))
    scenes = keep_season([row for row in fetched if row is not None])
    if len(scenes) < 2:
        raise SystemExit("fewer than two clear scenes")
    target = scenes[0][0].shape[:2]
    frames = []
    meta = []
    for rgb, stats in scenes:
        if rgb.shape[:2] != target:
            rgb = np.array(Image.fromarray(rgb).resize((target[1], target[0]), Image.Resampling.BILINEAR))
        Image.fromarray(rgb).save(os.path.join(args.out, stats["file"]))
        frames.append(Image.fromarray(rgb))
        meta.append(stats)
    fetch_s = round(time.perf_counter() - started, 2)
    print(f"fetched {len(frames)} years in {fetch_s}s", flush=True)

    height, width = target
    _, _, boxes = tiles(width, height)
    mask_pool = ThreadPoolExecutor(max_workers=1)
    mask_future = mask_pool.submit(read_masks, bbox)
    video_dir = os.path.join(args.out, "videos")
    os.makedirs(video_dir, exist_ok=True)

    def one(i, box):
        left, top, right, bottom, row, col = box
        crops = [frame.crop((left, top, right, bottom)) for frame in frames]
        path = os.path.join(video_dir, f"tile_{i}.mp4")
        make_video(crops, path)
        score, raw, seconds = ask_video(path)
        print(json.dumps({"tile": i, "score": score, "seconds": seconds}), flush=True)
        return {
            "id": i,
            "row": row,
            "col": col,
            "score": score,
            "seconds": seconds,
            "box": [left, top, right, bottom],
            "polygon": ring((left, top, right, bottom), (width, height), bbox),
            "raw": raw,
        }

    model_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(boxes)) as pool:
        windows = list(pool.map(lambda pair: one(*pair), list(enumerate(boxes))))
    model_s = round(time.perf_counter() - model_started, 2)
    loss_mask, forest_mask, mask_error = mask_future.result()
    mask_pool.shutdown(wait=False)

    pixel_mask = None
    if loss_mask is not None and forest_mask is not None:
        loss_img = resize_mask(loss_mask, width, height)
        forest_img = resize_mask(forest_mask, width, height)
        pixel_mask = np.zeros((height, width), np.float32)
        for window in windows:
            left, top, right, bottom = window["box"]
            loss_share = float(loss_img[top:bottom, left:right].mean())
            forest_share = float(forest_img[top:bottom, left:right].mean())
            window["loss_share"] = round(loss_share, 3)
            window["forest_share"] = round(forest_share, 3)
            window["eligible"] = forest_share >= 0.5 and loss_share >= 0.08
            if window["eligible"]:
                pixel_mask[top:bottom, left:right] = (loss_img[top:bottom, left:right] & forest_img[top:bottom, left:right]).astype(np.float32)
            elif window["score"] is not None:
                window["score_raw"] = window["score"]
                window["score"] = 0.0
        scored = [(w["loss_share"], w["score"]) for w in windows if w.get("eligible") and w["score"] is not None]
        weight = sum(share for share, _ in scored)
        mean_score = round(sum(share * score for share, score in scored) / weight, 3) if weight else 0.0
    else:
        scored = [w["score"] for w in windows if w["score"] is not None]
        mean_score = round(float(np.mean(scored)), 3) if scored else None

    Image.fromarray(paint((width, height), [(w["box"], w["score"]) for w in windows], pixel_mask)).save(
        os.path.join(args.out, "confidence.png")
    )
    confidence = {
        "model": "perceptron-mk1.5",
        "reasoning_effort": "high",
        "prompt": PROMPT,
        "gated": pixel_mask is not None,
        "mean_score": mean_score,
        "windows": windows,
        "fetch_s": fetch_s,
        "model_s": model_s,
        "total_s": round(time.perf_counter() - started, 2),
        "mask_error": mask_error,
    }
    with open(os.path.join(args.out, "confidence.json"), "w") as f:
        json.dump(confidence, f, indent=2)
        f.write("\n")
    evidence = {
        "lat": (bbox[1] + bbox[3]) / 2,
        "lon": (bbox[0] + bbox[2]) / 2,
        "bbox": bbox,
        "scenes": meta,
        "before": {k: meta[0][k] for k in ("date", "scene_id", "cloud_cover")},
        "after": {k: meta[-1][k] for k in ("date", "scene_id", "cloud_cover")},
    }
    frames[0].save(os.path.join(args.out, "before.png"))
    frames[-1].save(os.path.join(args.out, "after.png"))
    with open(os.path.join(args.out, "evidence.json"), "w") as f:
        json.dump(evidence, f, indent=2)
        f.write("\n")
    print(f"total {confidence['total_s']}s fetch {fetch_s}s model {model_s}s mean {confidence['mean_score']}", flush=True)


if __name__ == "__main__":
    main()
