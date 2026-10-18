# Clearcut

Clearcut is an independent check a government can run when a plantation asks to be approved.

A plantation that wants approval must submit the geographic location of every plot. Clearcut takes one submitted location and builds the record for that ground back to the EU deforestation cutoff of 31 December 2020. The government decides whether to approve it. Clearcut returns the satellite record and whether that record supports approval.

The check is public. Approval rests on the images and the loss map, rather than on the plantation's own description of the land. People who live with that forest can read the same pack.

## What a check shows

For the polygon the plantation submitted, Clearcut gathers four things:

1. A satellite image from before the cutoff and a current image of the same ground, from Sentinel-2.
2. The year any forest on that ground was lost, from Hansen Global Forest Change (loss years through 2024).
3. A description of what the land became. A vision model reads the current image and marks the converted regions.
4. A cross-check of each marked region against the loss map. A region is confirmed when the map shows substantial clearing inside it after 2020. Any other region stays inconclusive and needs a person to review it.

When the record supports it, the sentence Clearcut stands behind is:

> This plot shows post-2020 tree-cover loss consistent with plantation conversion.

That result means the submission does not support approval. A plot with no post-2020 loss, and no conversion in the imagery, is reported as consistent with a claim that the land was not cleared after the cutoff.

EU deforestation duties apply from 30 December 2026 for large and medium operators. Micro and small operators who already had that status by 31 December 2024 generally wait until 30 June 2027. Clearcut uses 31 December 2020 as the cutoff for the land itself: forest converted to agriculture after that date is the change the check is looking for.

## The case on file

One submitted location is included, in West Kalimantan, Indonesia (latitude −0.78, longitude 110.08).

| Record | What it shows |
| --- | --- |
| 14 September 2019 | Continuous forest, before the cutoff |
| 2023 | About 20% of the plot lost. Nearly all of the post-2020 loss falls in this year |
| 8 August 2026 | Plantation grid, straight access roads, and planting rows |
| Large lower region | Confirmed. 43% of the region was lost after 2020 |
| Small corner region | Inconclusive. 14% post-2020 loss inside it, which is not enough to confirm |

The loss map ends in 2024, so the 2026 image is evidence of what the land looks like now. It is not a 2026 detection in the Hansen layer.

On this submission, Clearcut's result is that the plot does not support approval.

## In this repository

The check for that plot is produced by three steps:

- `pipeline/fetch_plot.py` reads Hansen loss years for a lat/lon box and saves the clearest Sentinel-2 scene before the cutoff and a current scene.
- `pipeline/perceive.py` asks a vision model what changed and where, and draws those regions on the current image.
- `pipeline/verify.py` cross-checks each region against post-2020 Hansen loss and writes a confirmed or inconclusive result.

Precomputed inputs and outputs for the West Kalimantan plot live in `data/plots/kalbar1`.

## Evidence pack (results page)

Shareable results for a plot (before/after slider, peak-loss year, clearing date, Perceptron copy, JSON + PDF):

```bash
.venv/bin/python web/server.py
# Map:     http://127.0.0.1:8765/web/
# Results: http://127.0.0.1:8765/web/results.html?plot=kalbar1
# JSON:    http://127.0.0.1:8765/api/audit/kalbar1/export
```

`pipeline/build_audit.py` assembles the pack from existing plot files (Hansen dates the clearing peak; no GFW API).
