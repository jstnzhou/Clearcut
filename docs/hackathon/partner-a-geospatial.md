# Partner A — Geospatial numbers & verdict

**You own the truth of the hectare counts and the tier.** Frontend and imagery plug into your audit record; they must not invent metrics.

Read [`00-shared.md`](./00-shared.md) first. Schema in §4 is yours to publish and protect.

## Mission

End-to-end: **valid plot geometry → baseline forest at cutoff → post-cutoff loss → verdict + reasons**.

This is priority #1 on the shared build list. Without you, there is no product.

## Owns (files)

```
backend/main.py          # POST /audit, GET /audit/{id}; leave PDF/export/assets routes for B
backend/geometry.py
backend/gfw_client.py
backend/baseline.py
backend/verdict.py
backend/tests/test_geometry.py
backend/tests/test_verdict.py
backend/cache/audits/{id}/audit.json   # write live results here
```

You may stub empty `imagery` / `perceptron` blocks so C can render; B fills them later.

## Does not own

- Sentinel composites, timelapse, PDF, Perceptron prompts → **B**
- Map UI, draw tool, slider, share page chrome → **C**

## Hour-by-hour (guide)

| Phase | Deliverable |
| --- | --- |
| 0–1h | FastAPI app up; `POST /audit` accepts GeoJSON, writes stub audit matching shared schema, returns `{id}` |
| 1–3h | `geometry.py`: parse GeoJSON/KML/GPX (KML via agreed client lib or accept GeoJSON-only first); `make_valid`; WGS84; geodesic `area_ha`; point+area buffer; reject self-intersecting; warn if area > 10,000 ha |
| 3–7h | `gfw_client.py`: Hansen loss 2021+ inside plot; integrated alerts ≥ 2021-01-01; per-year ha; 100 m edge band ha. Verify dataset names/fields against live GFW docs |
| 5–8h | `baseline.py`: JRC GFC2020 + Hansen 2000 density @ 10% minus loss through 2020; disagreement ha |
| 7–10h | `verdict.py` pure function + config thresholds + unit tests; wire into `/audit` |
| 10h+ | Help B hook after imagery/Perceptron; keep schema stable; freeze demo numbers with B |

If GFW is slow or flaky: Earth Engine `reduceRegion` fallback **only after** primary path works once.

## Land input rules (backend validation)

Mirror what C collects:

1. Polygon (draw or upload) — primary
2. Point + hectares (≤ 4 ha EUDR point mode) → buffer circle, set `geometry_approximate: true`
3. Uploads: GeoJSON preferred; KML → GeoJSON; GPX if trivial

## Verdict rules (implement exactly)

Config object (tunable):

```python
THRESHOLDS = {
    "non_compliant_loss_ha": 0.5,
    "edge_band_m": 100,
    "canopy_threshold_pct": 10,
    # "meaningful" baseline disagreement — pick a fraction or ha floor and document it
    "baseline_disagreement_ha": 0.5,
}
```

- **non_compliant:** post-cutoff forest loss ≥ 0.5 ha confirmed by Hansen or high-confidence alert
- **review:** any shared-doc Review condition (including flags B sets: `perceptron.disagrees_with_numbers`, cloudy imagery)
- **compliant:** no post-cutoff loss on cutoff-forest; sources agree

Every verdict returns `reasons: list[str]` in plain language.

## GFW sketch (verify before coding)

```
POST https://data-api.globalforestwatch.org/dataset/{dataset}/{version}/query/json
Header: x-api-key: $GFW_API_KEY
Body: { "geometry": <GeoJSON>, "sql": "..." }
```

Datasets (approx — **confirm IDs**):

- `umd_tree_cover_loss` — annual loss, years ≥ 2021 only for compliance math
- `gfw_integrated_alerts` (name TBD) — GLAD-L / GLAD-S2 / RADD, date ≥ 2021-01-01
- Hansen density threshold param: `umd_tree_cover_density_2000__threshold = 10`

JRC: Earth Engine / published GFC2020 — confirm current collection ID (`JRC/GFC2020/...`).

## Handoffs

**To C (frontend):**

- Stable `GET /audit/{id}` as early as possible (stubs OK)
- Document any query params for demo IDs (`?demo=kalbar1`)

**To B (imagery / AI):**

- Final plot geometry + `area_ha` + verdict tier before they run expensive STAC/Perceptron
- Prefer: A completes numbers → B enriches same `audit.json` → C always reads one record
- Agreement rule: if B’s Perceptron conflicts with your loss/baseline, B sets `perceptron.disagrees_with_numbers: true` and you re-run or expose a helper to bump tier → **Review**

**From README Kalbar plot:** lat −0.78, lon 110.08 — good Non-compliant fixture once you have a polygon (expand a box or pull a drawn boundary from GFW).

## Definition of done

- [ ] One real polygon returns live loss-by-year + tier + reasons
- [ ] Point+area path sets `geometry_approximate`
- [ ] `pytest` covers verdict tiers and geometry validation
- [ ] Schema matches shared §4; breaking changes ping the team
- [ ] Demo audit JSON for slots 1–3 written with B under `cache/`

## Cut if behind

Keep: geometry validation, Hansen post-2021 loss, verdict reasons.  
Defer: edge-band nuance, JRC (Hansen-only baseline with Review note), alerts, EE fallback.
