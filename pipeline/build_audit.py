"""Assemble a shareable audit pack from a plot folder's existing artifacts.

Usage:
    python pipeline/build_audit.py data/plots/kalbar1
    python pipeline/build_audit.py data/plots/kalbar1 --write
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

EUDR_CUTOFF = "2020-12-31"
PEAK_MIN_SHARE = 0.01  # ignore noise when picking peak / first clearing year


def load_json(path: Path):
    if not path.is_file():
        return None
    with path.open() as f:
        return json.load(f)


def scene_index(scenes: list, year: int | None):
    if year is None or not scenes:
        return None
    for i, scene in enumerate(scenes):
        if int(scene.get("year") or str(scene.get("date", "0"))[:4]) == year:
            return i
    # nearest year at or after target
    best = None
    best_delta = None
    for i, scene in enumerate(scenes):
        y = int(scene.get("year") or str(scene.get("date", "0"))[:4])
        delta = abs(y - year)
        if best_delta is None or delta < best_delta:
            best, best_delta = i, delta
    return best


def dating_from_hansen(loss_by_year: dict | None):
    """Date clearing from Hansen annual loss shares (no GFW API)."""
    if not loss_by_year:
        return {
            "method": "unavailable",
            "first_clearing_year": None,
            "peak_loss_year": None,
            "peak_loss_share": None,
            "post_cutoff_loss_share": None,
            "by_year": {},
            "note": "No Hansen loss series on this plot.",
        }

    post = {
        int(y): float(s)
        for y, s in loss_by_year.items()
        if int(y) > 2020 and float(s) > 0
    }
    post_cutoff = round(sum(post.values()), 4)
    peak_year = None
    peak_share = None
    if post:
        peak_year, peak_share = max(post.items(), key=lambda item: item[1])
        if peak_share < PEAK_MIN_SHARE:
            peak_year, peak_share = None, None

    first_year = None
    for year in sorted(post):
        if post[year] >= PEAK_MIN_SHARE:
            first_year = year
            break

    note = (
        "Clearing dated from Hansen annual tree-cover loss inside the plot. "
        "The loss map ends in 2024; later Sentinel frames show land use, not a new Hansen year."
    )
    if first_year and peak_year:
        relative = "before" if first_year <= 2020 else "after"
        note = (
            f"Hansen first shows meaningful post-cutoff loss in {first_year} "
            f"({relative} the EUDR cutoff of 31 Dec 2020). "
            f"Peak annual loss is {peak_year} ({peak_share:.1%} of the plot). "
            "Later Sentinel frames show current land use; they are not 2025–26 Hansen detections."
        )

    return {
        "method": "hansen_annual_loss",
        "cutoff": EUDR_CUTOFF,
        "first_clearing_year": first_year,
        "peak_loss_year": peak_year,
        "peak_loss_share": peak_share,
        "post_cutoff_loss_share": post_cutoff,
        "by_year": {str(y): post[y] for y in sorted(post)},
        "note": note,
        "relative_to_cutoff": (
            "after"
            if first_year and first_year > 2020
            else ("before_or_none" if not first_year else "on_or_before")
        ),
    }


def plot_name(plot_dir: Path) -> str:
    return plot_dir.name


def asset_url(plot: str, name: str | None) -> str | None:
    if not name:
        return None
    return f"/data/plots/{plot}/{name}"


def build_audit(plot_dir: Path) -> dict:
    plot_dir = plot_dir.resolve()
    plot = plot_name(plot_dir)
    evidence = load_json(plot_dir / "evidence.json") or {}
    verdict = load_json(plot_dir / "verdict.json")
    perception = load_json(plot_dir / "perception.json")
    confidence = load_json(plot_dir / "confidence.json")
    chip_stats = load_json(plot_dir / "chip_stats.json")
    perception_tiles = load_json(plot_dir / "perception_tiles.json")
    dating_file = load_json(plot_dir / "dating.json")

    hansen = evidence.get("hansen") or {}
    dating = dating_file or dating_from_hansen(hansen.get("loss_share_by_year"))

    scenes = list(evidence.get("scenes") or [])
    if not scenes and evidence.get("before"):
        scenes = [
            {**evidence["before"], "file": "before.png"},
            {**evidence.get("after", {}), "file": "after.png"},
        ]

    peak_year = dating.get("peak_loss_year")
    peak_idx = scene_index(scenes, peak_year)
    peak_scene = scenes[peak_idx] if peak_idx is not None else None

    before = evidence.get("before") or (scenes[0] if scenes else {})
    after = evidence.get("after") or (scenes[-1] if scenes else {})
    before_file = "before.png" if (plot_dir / "before.png").is_file() else (scenes[0].get("file") if scenes else None)
    after_file = "after.png" if (plot_dir / "after.png").is_file() else (scenes[-1].get("file") if scenes else None)
    peak_file = peak_scene.get("file") if peak_scene else None

    # Prefer plot-level timelapse if present; else list tile videos.
    timelapse = None
    if (plot_dir / "timelapse.mp4").is_file():
        timelapse = asset_url(plot, "timelapse.mp4")
    videos_dir = plot_dir / "videos"
    tile_videos = []
    if videos_dir.is_dir():
        tile_videos = [
            asset_url(plot, f"videos/{p.name}")
            for p in sorted(videos_dir.glob("*.mp4"))
        ]

    sentence = None
    if verdict and "CONTRADICTS" in str(verdict.get("verdict", "")):
        sentence = "This plot shows post-2020 tree-cover loss consistent with plantation conversion."
    elif confidence and isinstance(confidence.get("mean_score"), (int, float)) and confidence["mean_score"] >= 0.45:
        sentence = "This plot shows post-2020 tree-cover loss consistent with plantation conversion."
    elif verdict and "CONSISTENT" in str(verdict.get("verdict", "")):
        sentence = "The record is consistent with a claim that the land was not cleared after the cutoff."
    elif verdict and "INCONCLUSIVE" in str(verdict.get("verdict", "")):
        sentence = "Signals disagree; this plot needs human review."
    else:
        sentence = "Screening record assembled from public satellite data."

    boxes = []
    if verdict and isinstance(verdict.get("boxes"), list):
        boxes = verdict["boxes"]
    elif perception and isinstance(perception.get("boxes"), list):
        boxes = perception["boxes"]

    audit = {
        "id": plot,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "share_path": f"/web/results.html?plot={plot}",
        "plot": {
            "name": plot,
            "lat": evidence.get("lat"),
            "lon": evidence.get("lon"),
            "bbox": evidence.get("bbox"),
        },
        "cutoff": EUDR_CUTOFF,
        "verdict": {
            "summary": sentence,
            "raw": (verdict or {}).get("verdict"),
            "plot_post_cutoff_loss_share": (verdict or {}).get("plot_post_cutoff_loss_share")
            or hansen.get("loss_share_post_cutoff"),
            "boxes": boxes,
            "disclaimer": "Screening tool based on public satellite data. Not a legal certification.",
        },
        "dating": dating,
        "imagery": {
            "before": {
                **(before or {}),
                "file": before_file,
                "url": asset_url(plot, before_file),
            },
            "after": {
                **(after or {}),
                "file": after_file,
                "url": asset_url(plot, after_file),
            },
            "peak": {
                "year": peak_year,
                "file": peak_file,
                "url": asset_url(plot, peak_file),
                "scene": peak_scene,
                "loss_share": dating.get("peak_loss_share"),
            }
            if peak_file
            else None,
            "scenes": [
                {**s, "url": asset_url(plot, s.get("file"))}
                for s in scenes
                if s.get("file")
            ],
            "timelapse_url": timelapse,
            "tile_videos": tile_videos,
        },
        "perceptron": {
            "description": (perception or {}).get("change"),
            "model": (confidence or {}).get("model") or "perceptron-mk1.5",
            "mean_score": (confidence or {}).get("mean_score"),
            "boxes": boxes,
            "label": "AI interpretation",
        },
        "hansen": hansen,
        "jrc_source": (chip_stats or {}).get("jrc_source"),
        "chip_stats": chip_stats,
        "perception_tiles": perception_tiles,
        "confidence": confidence,
        "methodology": {
            "baseline": "JRC GFC2020 forest share on chips when available; Hansen tree-cover loss years for dating",
            "imagery": "Sentinel-2 L2A via Microsoft Planetary Computer",
            "vision": "Perceptron Mk1.5 describes conversion; does not supply hectare counts",
            "cutoff": EUDR_CUTOFF,
        },
    }
    return audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("plot")
    parser.add_argument("--write", action="store_true", help="Write audit.json and dating.json into the plot folder")
    args = parser.parse_args()
    plot_dir = Path(args.plot)
    audit = build_audit(plot_dir)
    text = json.dumps(audit, indent=2) + "\n"
    if args.write:
        dating_path = plot_dir / "dating.json"
        if not dating_path.is_file():
            with dating_path.open("w") as f:
                json.dump(audit["dating"], f, indent=2)
                f.write("\n")
        with (plot_dir / "audit.json").open("w") as f:
            f.write(text)
    print(text, end="")


if __name__ == "__main__":
    main()
