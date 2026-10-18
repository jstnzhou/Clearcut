"""Cross-check Perceptron's boxes against Hansen forest-loss years.

Usage:
    python pipeline/verify.py data/plots/kalbar1

A box is "confirmed" only when Hansen also shows substantial post-cutoff loss inside it;
otherwise it's "unconfirmed" and needs human review. Writes verdict.json.
"""
import json
import os
import sys

import numpy as np
import rasterio

from fetch_plot import EUDR_CUTOFF_YEAR, HANSEN_VERSION, hansen_tile

CONFIRM_SHARE = 0.25  # min share of box pixels with post-cutoff loss to confirm


def hansen_window(bbox, lat, lon):
    url = (
        f"/vsicurl/https://storage.googleapis.com/earthenginepartners-hansen/"
        f"{HANSEN_VERSION}/Hansen_{HANSEN_VERSION}_lossyear_{hansen_tile(lat, lon)}.tif"
    )
    with rasterio.open(url) as src:
        return src.read(1, window=rasterio.windows.from_bounds(*bbox, transform=src.transform))


def main():
    plot = sys.argv[1]
    evidence = json.load(open(os.path.join(plot, "evidence.json")))
    perception = json.load(open(os.path.join(plot, "perception.json")))
    loss = hansen_window(evidence["bbox"], evidence["lat"], evidence["lon"])
    post = (loss > EUDR_CUTOFF_YEAR - 2000) & (loss > 0)
    h, w = post.shape

    boxes = []
    for b in perception["boxes"]:
        # Box coords are 0-1000 over the image, which covers the same bbox as the Hansen window.
        x1, y1, x2, y2 = b["box"]
        sub = post[int(y1 * h / 1000) : max(int(y2 * h / 1000), 1), int(x1 * w / 1000) : max(int(x2 * w / 1000), 1)]
        share = float(sub.mean()) if sub.size else 0.0
        boxes.append({**b, "post_cutoff_loss_share": round(share, 3), "confirmed": share >= CONFIRM_SHARE})

    confirmed = [b for b in boxes if b["confirmed"]]
    if confirmed:
        verdict = "CONTRADICTS a no-deforestation-since-2020 declaration"
    elif evidence["hansen"]["loss_share_post_cutoff"] > 0.05 or boxes:
        verdict = "INCONCLUSIVE: signals disagree, needs human review"
    else:
        verdict = "CONSISTENT with a no-deforestation-since-2020 declaration"

    out = {
        "verdict": verdict,
        "boxes": boxes,
        "plot_post_cutoff_loss_share": evidence["hansen"]["loss_share_post_cutoff"],
        "loss_years": {y: s for y, s in evidence["hansen"]["loss_share_by_year"].items() if int(y) > EUDR_CUTOFF_YEAR},
    }
    with open(os.path.join(plot, "verdict.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
