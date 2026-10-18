"""Ask Perceptron what changed in a plot and where.

Usage:
    python pipeline/perceive.py data/plots/kalbar1

Reads before.png / after.png from the plot folder, writes perception.json and after_boxes.png.
API key: PERCEPTRON_API_KEY env var, or the first line of ~/Desktop/perceptron-key.txt.
"""
import json
import os
import re
import sys

from PIL import Image, ImageDraw
from perceptron import Client, image

MODEL = "perceptron-mk1.5"
KEY_FILE = os.path.expanduser("~/Desktop/perceptron-key.txt")


def api_key():
    if os.environ.get("PERCEPTRON_API_KEY"):
        return os.environ["PERCEPTRON_API_KEY"]
    with open(KEY_FILE) as f:
        return f.readline().strip()


def ask(client, images, prompt, annotation_format=None):
    content = [image(p) for p in images] + [{"type": "text", "text": prompt}]
    kwargs = {"vision_config": {"annotation_format": annotation_format}} if annotation_format else {}
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": content}],
        max_completion_tokens=2048,
        **kwargs,
    )
    return response.choices[0].message.content or ""


def parse_boxes(text):
    # Boxes come back as <point_box ...> markup with coordinates on a 0-1000 grid.
    # Long answers can degenerate into repeated zero-area boxes, so drop those and duplicates.
    boxes, seen = [], set()
    for m in re.finditer(r"<point_box([^>]*)>(.*?)</point_box>", text, re.S):
        nums = [int(n) for n in re.findall(r"\d+", m.group(2))]
        if len(nums) < 4:
            continue
        x1, y1, x2, y2 = nums[:4]
        if x2 - x1 < 5 or y2 - y1 < 5 or (x1, y1, x2, y2) in seen:
            continue
        seen.add((x1, y1, x2, y2))
        label = re.search(r'mention="([^"]*)"', m.group(1))
        boxes.append({"label": label.group(1) if label else "", "box": [x1, y1, x2, y2]})
    return boxes


def draw_boxes(src, boxes, dst):
    img = Image.open(src).convert("RGB")
    w, h = img.size
    d = ImageDraw.Draw(img)
    for b in boxes:
        x1, y1, x2, y2 = b["box"]
        d.rectangle([x1 * w / 1000, y1 * h / 1000, x2 * w / 1000, y2 * h / 1000], outline=(255, 40, 40), width=3)
    img.save(dst)


def main():
    plot = sys.argv[1]
    before, after = os.path.join(plot, "before.png"), os.path.join(plot, "after.png")
    client = Client(api_key=api_key())

    result = {
        "change": ask(
            client,
            [before, after],
            "These are Sentinel-2 satellite images of the same area: the first from 2019, the second from 2026. "
            "Describe what changed in land cover. Is there evidence that forest was converted to a plantation "
            "(grid-like blocks, straight access roads, planting rows)? Be specific and say if you are unsure.",
        ),
    }
    boxes_text = ask(
        client,
        [after],
        "Locate the converted land in this satellite image: put one bounding box around each large contiguous "
        "area of plantation blocks or cleared land. Use at most 6 boxes; do not box individual rows.",
        annotation_format="box",
    )
    result["boxes_raw"] = boxes_text
    result["boxes"] = parse_boxes(boxes_text)
    draw_boxes(after, result["boxes"], os.path.join(plot, "after_boxes.png"))

    with open(os.path.join(plot, "perception.json"), "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
