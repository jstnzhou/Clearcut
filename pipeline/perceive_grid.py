"""Confidence grid for one plot. Overlapping windows, one score each, painted as a gradient.

Usage:
    python pipeline/perceive_grid.py data/plots/kalbar1

Writes confidence.json and confidence.png. API key: PERCEPTRON_API_KEY, or the
first line of ~/Desktop/perceptron-key.txt.
"""
import json
import os
import re
import sys

import numpy as np
from PIL import Image, ImageFilter
from perceptron import Client, image

MODEL = "perceptron-mk1.5"
KEY_FILE = os.path.expanduser("~/Desktop/perceptron-key.txt")
PROMPT = (
    "The first image is an earlier Sentinel-2 satellite chip. The second is the same ground on a later date. "
    "Reply with one number from 0.0 to 1.0 and nothing else. "
    "The number is your confidence that forest in the first image was converted to plantation in the second: "
    "rectangular blocks, straight access roads, or planting rows. "
    "0.0 means still forest or no conversion. 1.0 means a clear plantation conversion. "
    "Use a value in between when the chip is mixed or you are unsure."
)


def api_key():
    if os.environ.get("PERCEPTRON_API_KEY"):
        return os.environ["PERCEPTRON_API_KEY"]
    with open(KEY_FILE) as f:
        return f.readline().strip()


def parse_score(text):
    match = re.search(r"\d+(?:\.\d+)?", text or "")
    if not match:
        return None
    value = float(match.group())
    if value > 1:
        value = value / 100 if value <= 100 else 1
    return round(max(0.0, min(1.0, value)), 3)


def window_boxes(width, height, across=5):
    # Window is twice the stride, so neighbors overlap by half and the scores blend.
    step_x = width / (across + 1)
    step_y = height / (across + 1)
    win_w = 2 * step_x
    win_h = 2 * step_y
    boxes = []
    for row in range(across):
        for col in range(across):
            left = int(round(col * step_x))
            top = int(round(row * step_y))
            right = min(width, int(round(left + win_w)))
            bottom = min(height, int(round(top + win_h)))
            boxes.append((left, top, right, bottom))
    return boxes


def pixel_ring(box, size, bbox):
    left, top, right, bottom = box
    width, height = size
    min_lon, min_lat, max_lon, max_lat = bbox
    def lon(x):
        return min_lon + (max_lon - min_lon) * (x / width)
    def lat(y):
        return max_lat - (max_lat - min_lat) * (y / height)
    ring = [[lon(left), lat(top)], [lon(right), lat(top)], [lon(right), lat(bottom)], [lon(left), lat(bottom)]]
    ring.append(ring[0])
    return ring


def ask(client, before_path, after_path):
    content = [image(before_path), image(after_path), {"type": "text", "text": PROMPT}]
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": content}],
        max_completion_tokens=1024,
        reasoning_effort="medium",
        vision_config={"annotation_format": "box"},
    )
    return response.choices[0].message.content or ""


def colorize(scores):
    # Green at 0, gold in the middle, red at 1. Alpha rises with the score.
    out = np.zeros(scores.shape + (4,), np.uint8)
    low = scores <= 0.5
    high = ~low
    t = np.zeros(scores.shape, np.float32)
    t[low] = scores[low] / 0.5
    t[high] = (scores[high] - 0.5) / 0.5
    out[low, 0] = (30 + (183 - 30) * t[low]).astype(np.uint8)
    out[low, 1] = (132 + (149 - 132) * t[low]).astype(np.uint8)
    out[low, 2] = (73 + (11 - 73) * t[low]).astype(np.uint8)
    out[high, 0] = (183 + (192 - 183) * t[high]).astype(np.uint8)
    out[high, 1] = (149 + (57 - 149) * t[high]).astype(np.uint8)
    out[high, 2] = (11 + (43 - 11) * t[high]).astype(np.uint8)
    out[:, :, 3] = (30 + 170 * scores).astype(np.uint8)
    return out


def paint(size, boxes, scores):
    width, height = size
    total = np.zeros((height, width), np.float32)
    weight = np.zeros((height, width), np.float32)
    for box, score in zip(boxes, scores):
        if score is None:
            continue
        left, top, right, bottom = box
        total[top:bottom, left:right] += score
        weight[top:bottom, left:right] += 1
    filled = weight > 0
    total[filled] /= weight[filled]
    blurred = np.array(
        Image.fromarray((total * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(radius=18)),
        dtype=np.float32,
    ) / 255
    blurred[~filled] = 0
    return colorize(np.clip(blurred, 0, 1))


def main():
    plot = sys.argv[1]
    evidence = json.load(open(os.path.join(plot, "evidence.json")))
    before = Image.open(os.path.join(plot, "before.png")).convert("RGB")
    after = Image.open(os.path.join(plot, "after.png")).convert("RGB")
    if after.size != before.size:
        after = after.resize(before.size, Image.Resampling.BILINEAR)
    boxes = window_boxes(*before.size)
    chip_dir = os.path.join(plot, "windows")
    os.makedirs(chip_dir, exist_ok=True)
    client = Client(api_key=api_key())
    windows = []
    scores = []
    for i, box in enumerate(boxes):
        before_path = os.path.join(chip_dir, f"before_{i}.png")
        after_path = os.path.join(chip_dir, f"after_{i}.png")
        before.crop(box).save(before_path)
        after.crop(box).save(after_path)
        text, error = None, None
        for attempt in range(2):
            try:
                text = ask(client, before_path, after_path)
                error = None
                break
            except Exception as exc:
                error = str(exc)
        score = parse_score(text) if text else None
        scores.append(score)
        windows.append({
            "id": i,
            "score": score,
            "box": list(box),
            "polygon": pixel_ring(box, before.size, evidence["bbox"]),
            "raw": text,
            "error": error,
        })
        print(json.dumps({"id": i, "score": score}), flush=True)

    Image.fromarray(paint(before.size, boxes, scores)).save(os.path.join(plot, "confidence.png"))
    known = [s for s in scores if s is not None]
    out = {
        "model": MODEL,
        "reasoning_effort": "medium",
        "prompt": PROMPT,
        "window": "5 by 5 overlapping windows, each one half-overlapping its neighbors",
        "mean_score": round(float(np.mean(known)), 3) if known else None,
        "windows": windows,
    }
    dest = os.path.join(plot, "confidence.json")
    with open(dest, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    print(f"wrote {dest} mean {out['mean_score']}", flush=True)


if __name__ == "__main__":
    main()
