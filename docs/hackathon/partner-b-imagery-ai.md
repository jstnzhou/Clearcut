# Partner B — Imagery, Perceptron, evidence pack

**You own the proof the audience can see and the pack a buyer can download.** Numbers still come from Partner A; you explain and package them.

Read [`00-shared.md`](./00-shared.md) first.

## Mission

1. Sentinel-2 **before / after** (and quarterly timelapse) clipped to the plot  
2. Perceptron **A** (explain change) then **B** (date clearing from video)  
3. **Evidence pack PDF** + machine-readable export  
4. **Demo cache** so stage never depends on live APIs  

## Owns (files)

```
backend/sentinel.py
backend/perceptron_client.py
backend/evidence_pack.py
backend/cache/demo/                 # precomputed audits + assets
backend/templates/evidence_pack.html
backend/main.py                    # only: /pdf, /export, /assets routes (coordinate with A)
```

Single Perceptron interface in `perceptron_client.py` — hide Mk/Isaac details behind it. Check workshop docs for endpoint, multi-image, and video input format; do not invent.

## Does not own

- GFW/Hansen/JRC hectare math or verdict thresholds → **A**
- Leaflet draw UI, slider chrome, Recharts timeline → **C** (you supply image URLs)

## Hour-by-hour (guide)

| Phase | Deliverable |
| --- | --- |
| 0–1h | Agree asset paths with A/C (`/audit/{id}/assets/before.png` etc.); stub empty PNGs |
| 1–5h | `sentinel.py`: Planetary Computer STAC `sentinel-2-l2a`; cloud-masked (SCL) median; before ≈ Oct–Dec 2020; after = last 3 months; fixed identical RGB stretch (B04/B03/B02); clip to plot + margin; record dates + cloud % |
| 5–8h | Wire assets into audit record; C’s before/after slider works on a real plot |
| 6–10h | Perceptron A: side-by-side labeled chip → strict JSON (`change_detected`, `driver`, `description`, `confidence`, `regions`). Run on loss plots + one clean control |
| 10–13h | Evidence pack PDF (WeasyPrint) + `/export` GeoJSON/JSON |
| 11–15h | Perceptron B: quarterly composites → short video (1–2 fps, date burned in) → clearing quarter; map to timeline marker for C |
| 13–16h | Freeze 3–4 demo audits into `cache/demo/` with all assets; `DEMO_MODE` short-circuit |

## Sentinel rules

- Source: Microsoft Planetary Computer STAC via `pystac-client` + `planetary-computer` + `stackstac` / `odc-stac` (EE fallback only if STAC blocked)
- Before window: Oct–Dec 2020; widen if too cloudy
- After: most recent ~3 months, same method
- Quarterly composites 2020-Q4 → present for timelapse
- Imagery is **evidence**, not the detection source for hectares

## Perceptron rules

**A — Explain (required)**  
Prompt context must include: Sentinel-2, 10 m/px, approx. span, north up, left/right dates, region, yellow plot outline.  
Parse strict JSON; retry on malformed output.

**B — Date clearing (cuttable)**  
`ffmpeg` / `imageio` video → model returns frame/timestamp → map to quarter. Answers “before or after cutoff?” for the pitch.

**Agreement:** if description/date conflicts with A’s Hansen/alerts (e.g. “no change” where 2 ha loss, or pre-2021 date when loss is 2022), set `perceptron.disagrees_with_numbers: true` and ensure verdict becomes **Review** (call A’s helper or write the flag A already keys off).

Never let Perceptron overwrite `loss.*` or `baseline.*`.

## Evidence pack PDF (required — do not cut)

Include:

- Plot map image + coordinates, area, input method, approximate flag  
- Verdict + all reasons  
- Before/after with acquisition date ranges and cloud %  
- Per-year loss table with cutoff line at 31 Dec 2020  
- Perceptron findings labeled **AI interpretation**  
- Methodology: dataset **versions**, canopy threshold 10%, verdict rules, query date  
- Disclaimer  

Export: GeoJSON Feature (geometry) + JSON audit record suitable for an importer’s due-diligence file.

## Demo mode (critical — you drive this)

Precompute:

1. Compliant  
2. Non-compliant (post-2021 plantation/pasture) — Kalbar README case is a candidate  
3. Review (edge / disagreement / cloudy)  
4. Optional: pre-2020 loss still Compliant  

`DEMO_MODE=true` serves these without calling GFW/STAC/Perceptron. Pick plots on globalforestwatch.org early; generate cache once live path works.

## Handoffs

**From A:** final geometry + filled `baseline` / `loss` / `verdict` before expensive runs.  

**To C:** stable asset URLs; Perceptron `description`, `regions`, `clearing_quarter` for map overlay + timeline marker.  

**To A:** `disagrees_with_numbers`, cloud failure flags so Review triggers correctly.

## Definition of done

- [ ] Real before/after PNGs for ≥1 live plot with dates + cloud % in audit JSON  
- [ ] Perceptron A returns parsed JSON on a loss plot  
- [ ] PDF downloads with methodology + disclaimer  
- [ ] `/export` returns GeoJSON + audit JSON  
- [ ] Demo cache covers Compliant / Non-compliant / Review; stage path uses it  

## Cut if behind

Keep: before/after composites, Perceptron A, PDF, demo cache.  
Defer: Perceptron B (video dating), fancy alert overlays, EE fallback, quarterly timelapse polish.
