"""Classify change on four fixed chips of a plot with Perceptron Mk1.5.

Usage:
    python pipeline/perceive_tiles.py data/plots/kalbar1

Crops before.png / after.png into NW, NE, SW, SE chips, asks one label per chip,
and writes perception_tiles.json. API key: PERCEPTRON_API_KEY, or the first line
of ~/Desktop/perceptron-key.txt.
"""
import json
import os
import re
import sys

from PIL import Image
from perceptron import Client, image

MODEL = "perceptron-mk1.5"
KEY_FILE = os.path.expanduser("~/Desktop/perceptron-key.txt")
LABELS = ("forest_unchanged", "converted_to_plantation", "unclear")
PROMPT = (
    "The first image is an earlier Sentinel-2 satellite chip. The second is the same ground on a later date. "
    "Classify the change as exactly one label: forest_unchanged, converted_to_plantation, or unclear. "
    "Use converted_to_plantation only when forest has been replaced by rectangular blocks, straight access roads, "
    "or planting rows. Put the label alone on the first line. If you choose converted_to_plantation, add at most "
    "one bounding box around that converted area. If you are unsure, answer unclear."
)


def api_key():
    if os.environ.get("PERCEPTRON_API_KEY"):
        return os.environ["PERCEPTRON_API_KEY"]
    with open(KEY_FILE) as f:
        return f.readline().strip()


def parse_label(text):
    for line in text.splitlines():
        label = line.strip()
        if label:
            return label if label in LABELS else "unclear"
    return "unclear"


def parse_one_box(text):
    # Same markup as perceive.py: <point_box> coordinates on a 0-1000 grid. Keep the first non-zero box.
    for m in re.finditer(r"<point_box([^>]*)>(.*?)</point_box>", text, re.S):
        nums = [int(n) for n in re.findall(r"\d+", m.group(2))]
        if len(nums) < 4:
            continue
        x1, y1, x2, y2 = nums[:4]
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            continue
        return [x1, y1, x2, y2]
    return None


def ask_chip(client, before_path, after_path):
    content = [image(before_path), image(after_path), {"type": "text", "text": PROMPT}]
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": content}],
        max_completion_tokens=2048,
        reasoning_effort="high",
        vision_config={"annotation_format": "box"},
    )
    return response.choices[0].message.content or ""


def main():
    plot = sys.argv[1]
    before = Image.open(os.path.join(plot, "before.png")).convert("RGB")
    after = Image.open(os.path.join(plot, "after.png")).convert("RGB")
    if after.size != before.size:
        after = after.resize(before.size, Image.Resampling.BILINEAR)
    w, h = before.size
    mx, my = w // 2, h // 2
    chip_boxes = ((0, 0, mx, my), (mx, 0, w, my), (0, my, mx, h), (mx, my, w, h))
    chip_dir = os.path.join(plot, "chips")
    os.makedirs(chip_dir, exist_ok=True)

    client = Client(api_key=api_key())
    chips = []
    for i, box in enumerate(chip_boxes):
        before_path = os.path.join(chip_dir, f"before_{i}.png")
        after_path = os.path.join(chip_dir, f"after_{i}.png")
        before.crop(box).save(before_path)
        after.crop(box).save(after_path)

        text = None
        error = None
        for attempt in range(2):
            try:
                text = ask_chip(client, before_path, after_path)
                error = None
                break
            except Exception as exc:
                error = str(exc)
                if attempt == 0:
                    continue
        if error is not None:
            chips.append({"id": i, "label": None, "error": error})
            print(json.dumps(chips[-1]), flush=True)
            continue

        label = parse_label(text)
        chips.append({
            "id": i,
            "label": label,
            "box": parse_one_box(text) if label == "converted_to_plantation" else None,
            "raw": text,
        })
        print(json.dumps({"id": i, "label": label, "box": chips[-1]["box"]}), flush=True)

    out = {
        "model": MODEL,
        "reasoning_effort": "high",
        "chips": chips,
    }
    dest = os.path.join(plot, "perception_tiles.json")
    with open(dest, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    print(f"wrote {dest}", flush=True)


if __name__ == "__main__":
    main()
