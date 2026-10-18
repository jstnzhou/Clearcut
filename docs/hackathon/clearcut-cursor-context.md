---
description: End-to-end project context for Groundtruth (EUDR deforestation audit for farmers)
alwaysApply: true
---

# Groundtruth — Project Context

> Working name: **Groundtruth**. Hackathon: DubHacks 2026 (~18h build). Tracks: SpaceXAI (must be built with Cursor; heavy Cursor use is judged) + Perceptron perception track (Mk / Isaac models via provided API).

## 1. What we're building

A mobile-friendly web app where a **farmer or cooperative** enters a farm plot and gets a **deforestation audit** of that land against the **EU Deforestation Regulation (EUDR)**. The core question the app answers:

> **Was this land forest on 31 Dec 2020, and has any of it been cleared since?**

EUDR context (use in copy, keep accurate):
- EUDR prohibits placing products on the EU market if they come from land deforested after the **cutoff date of 31 Dec 2020**.
- Covered commodities: cattle, cocoa, coffee, oil palm, rubber, soy, wood.
- Main obligations apply from **30 Dec 2026** for most operators (30 Jun 2027 for certain micro/small operators).
- Plots **> 4 ha** require a full polygon; plots **≤ 4 ha** may be a single geolocation point.
- "Forest" follows the FAO definition: > 0.5 ha, trees > 5 m, canopy cover > 10%.

Why farmers need this: EU buyers face heavy due-diligence costs, so smallholders who can't cheaply prove their plots are clean risk being dropped from supply chains. Groundtruth gives them a free, shareable, evidence-backed audit.

**Positioning:** a *screening and evidence tool*, not a legal certification. Every verdict must be traceable to named datasets, dates, and rules.

## 2. Primary user

Farmer / cooperative field officer, often on a phone, possibly low bandwidth, not a GIS expert.
- Plain-language explanations ("Some trees were cleared here in 2022, after the EU cutoff date").
- Large tap targets, minimal jargon, mobile-first layout.
- A **share link** so they can send the audit to an EU buyer.

## 3. Land input (Decision 1: A + B)

1. **Draw on map**: polygon draw tool (Leaflet + leaflet-draw, or Mapbox GL Draw) over satellite basemap.
2. **Upload file**: GeoJSON or KML (parse KML → GeoJSON client-side, e.g. `@tmcw/togeojson`). Also accept a phone GPS boundary walk exported as GPX/GeoJSON if trivial.
3. **Point + area mode** for plots ≤ 4 ha: user drops a point and enters hectares; we buffer to a circle of that area and mark the geometry as "approximate" in all outputs.

Validation: valid, non-self-intersecting polygon (fix with `shapely.make_valid`), WGS84, area computed geodesically, warn if area > 10,000 ha (probably wrong).

## 4. Data pipeline

### 4.1 Baseline forest at cutoff (Decision 2: A + B)
- **Primary: JRC Global Forest Cover 2020 (GFC2020)** — the EU's own 10 m reference forest map for EUDR, representing forest at the cutoff. (Earth Engine collection approx. `JRC/GFC2020/...` — verify current ID.)
- **Secondary: Hansen/UMD Global Forest Change** — 2000 tree cover minus loss through 2020 = forest at cutoff.
- **Canopy threshold: use 10% for Hansen** so it matches the EU/FAO forest definition. The GFW API exposes `umd_tree_cover_density_2000__threshold`; query with threshold = 10. Display the threshold used in the evidence pack.
- If JRC and Hansen disagree on whether part of the plot was forest at the cutoff → that area contributes to a **Review** flag.

### 4.2 Post-cutoff loss — the numbers (Decision 3: B)
- **Hansen annual tree cover loss**, years **2021 onward** only (via GFW Data API, dataset `umd_tree_cover_loss`).
- **GFW integrated deforestation alerts** (GLAD-L, GLAD-S2, RADD radar — radar sees through clouds) dated **≥ 2021-01-01**, with confidence levels (via GFW Data API, dataset approx. `gfw_integrated_alerts` — verify name).
- Compute: hectares of post-cutoff loss **inside the plot, on land that was forest at cutoff**; per-year breakdown; separately, loss within a 100 m edge band.
- GFW Data API: `POST https://data-api.globalforestwatch.org/dataset/{dataset}/{version}/query/json` with `{"geometry": <GeoJSON>, "sql": "..."}` and header `x-api-key`. **Verify field names against the live docs before relying on them.** Fallback: Earth Engine `reduceRegion`.

### 4.3 Visual evidence — Sentinel-2 (Decision 3: imagery is evidence, not detection)
- Source: **Microsoft Planetary Computer** STAC (`sentinel-2-l2a`) via `pystac-client` + `planetary-computer` + `stackstac`/`odc-stac`. (Earth Engine as fallback.)
- **"Before"**: cloud-masked (SCL band) median composite, roughly Oct–Dec 2020 (widen window if too cloudy).
- **"After"**: same method, most recent 3 months.
- **Quarterly composites** 2020-Q4 → present for the timelapse (Section 5B).
- Render true-color RGB (B04, B03, B02) with a **fixed, identical contrast stretch** across all dates. Clip/outline to plot + small margin. Record image dates and cloud % for the evidence pack.

## 5. Perceptron usage (Decision 4: A + B)

Perceptron Mk / Isaac models are general vision-language models (not satellite-specific). Treat their output as **explanation**, never as the source of hectare numbers. Check API details (endpoint, multi-image support, video input format) from the Perceptron workshop docs; keep the client in `backend/perceptron_client.py` behind one interface.

**A. Explain the change (before/after chips)**
- Input: side-by-side composite image (left = Dec 2020, right = latest, labeled) — or two images if multi-image input is supported.
- Prompt includes context: "Sentinel-2, 10 m/pixel, ~X km across, north up, left = 2020-12, right = <date>, location <country/region>, plot outlined in yellow."
- Ask for strict JSON: `{change_detected: bool, driver: "plantation"|"pasture"|"cropland"|"road"|"mining"|"fire"|"settlement"|"none"|"unclear", description: str, confidence: "low"|"medium"|"high", regions: [{box, label}]}`.
- Only run on plots/areas where Hansen or alerts flag post-cutoff loss, plus a control call on clean plots for demo contrast.

**B. Date the clearing (video event finding)**
- Stitch quarterly Sentinel-2 composites into a short video (e.g. 1–2 fps, date label burned into each frame) with `ffmpeg`/`imageio`.
- Ask Perceptron to find when forest clearing begins inside the outlined plot; return frame/timestamp → map back to quarter.
- This directly answers EUDR's legal question: **before or after the cutoff?**

**Agreement rule:** if Perceptron's description/date conflicts with Hansen/alerts (e.g. says no change where Hansen shows 2 ha loss, or dates clearing pre-2021 while Hansen says 2022), surface the disagreement and push the verdict to **Review**.

## 6. Verdict logic (Decision 5: three tiers)

Implement as a pure, unit-tested function in `backend/verdict.py`. Every verdict returns a list of human-readable reasons.

- **🔴 Non-compliant**: ≥ 0.5 ha of post-cutoff loss inside the plot on land that was forest at cutoff (per JRC or Hansen), confirmed by Hansen loss or a high-confidence alert.
- **🟡 Review** (any of):
  - post-cutoff loss > 0 but < 0.5 ha;
  - loss only within the 100 m edge band;
  - JRC and Hansen disagree on baseline forest for a meaningful share of the plot;
  - Hansen and alerts disagree;
  - Perceptron disagrees with the numbers;
  - imagery too cloudy to show clear evidence;
  - plot entered in approximate point + area mode with any nearby loss.
- **🟢 Compliant**: no post-cutoff loss on cutoff-forest inside the plot, sources agree.

Thresholds live in one config object so they're easy to tune.

## 7. Presentation (Decision 7)

Single audit page, top to bottom:
1. **Verdict banner** — tier color, one-sentence plain-language reason, plot name/area.
2. **Before/after slider (hero)** — Sentinel-2 Dec 2020 vs. latest, plot outlined. This is the demo moment; make it smooth and big.
3. **Map** — plot outline, cutoff-forest shading, loss pixels colored by year with a clear visual split between **≤ 2020 (grey)** and **≥ 2021 (red shades)**, alert points; Perceptron bounding regions as optional overlay.
4. **Timeline strip (compact)** — annual loss bar chart, a bold vertical **cutoff line at 31 Dec 2020**, and a marker for the Perceptron-detected clearing date.
5. Perceptron's plain-language description of what replaced the forest.
6. Actions: **Download evidence pack**, **Export data**, **Copy share link**.

Persistent footer: "Screening tool based on public satellite data. Not a legal certification."

## 8. Deliverables (Decision 8: B + C)

**B. Evidence pack PDF** (generate server-side, e.g. WeasyPrint from an HTML template, or ReportLab):
- Plot geometry (map image + coordinates, area, input method, approximate flag)
- Verdict + all reasons
- Before/after images with exact acquisition date ranges and cloud %
- Per-year loss table with the cutoff line marked
- Perceptron findings (description, driver, detected clearing date) labeled as AI interpretation
- Methodology: datasets **with versions**, canopy threshold, verdict rules, query date
- Disclaimer

**C. Machine-readable export**: GeoJSON Feature (plot geometry) + JSON audit record (verdict, reasons, metrics, dataset versions, timestamps), structured so an EU importer could attach it to their due-diligence statement.

## 9. Architecture

```
frontend/   Next.js (App Router) + TypeScript + Tailwind; Leaflet for maps; before/after slider component; Recharts for timeline
backend/    Python FastAPI
  main.py               routes: POST /audit, GET /audit/{id}, GET /audit/{id}/pdf, GET /audit/{id}/export
  geometry.py           input parsing, validation, point+area buffering, geodesic area
  gfw_client.py         Hansen loss + alerts queries
  baseline.py           JRC GFC2020 + Hansen baseline forest
  sentinel.py           STAC search, cloud masking, composites, RGB rendering, quarterly timelapse
  perceptron_client.py  A (explain) + B (video dating), strict JSON parsing with retries
  verdict.py            pure function + config thresholds (unit tested)
  evidence_pack.py      PDF + JSON/GeoJSON export
  cache/                precomputed demo audits (JSON + images + video)
```

Env vars in `.env` (never commit): `GFW_API_KEY`, `PERCEPTRON_API_KEY`, optional Earth Engine credentials.

## 10. Demo mode (critical)

- `DEMO_MODE=true` serves **precomputed, cached** audits for 3–4 demo plots: one clearly **Compliant**, one clearly **Non-compliant** (post-2021 clearing for plantation/pasture), one **Review** (edge loss or source disagreement), optionally one with pre-2020 clearing that is still Compliant (shows we respect the cutoff).
- The live pipeline must still work for at least one plot, but the stage demo never depends on external APIs.
- Pick demo plots by browsing globalforestwatch.org for obvious, well-documented clearings.

## 11. Build priorities

1. Geometry input → GFW loss numbers → verdict (end-to-end with one plot)
2. Sentinel-2 before/after + slider
3. Map + timeline strip
4. Perceptron A, then B
5. Evidence pack PDF + JSON export
6. Share link, polish

Cut order if behind: share link → Perceptron B (video dating) → map overlays. Never cut: verdict reasons, before/after slider, evidence pack.

## 12. Working conventions for the agent

- Verify external dataset IDs, API fields, and Perceptron request formats against current docs before building on them; don't invent endpoints.
- Every number shown to users must trace back to a dataset query stored in the audit record.
- Keep claims honest in UI copy: say what the data shows and its limits.
- Write unit tests for `verdict.py` and `geometry.py`.
- Small, frequent commits with descriptive messages.
