# Shared context — Groundtruth (DubHacks 2026)

Working name in the Cursor brief: **Groundtruth**. Repo name today: **Clearcut**. Tracks: **SpaceXAI** (must be built with Cursor) + **Perceptron** (Mk / Isaac via provided API). Budget: ~18h.

## 1. Product lock (clarified)

The uploaded context and the repo README disagree. For this hackathon we lock:

| Question | Lock for DubHacks |
| --- | --- |
| Primary user | **Farmer / cooperative field officer** on a phone (context doc wins for the build) |
| Core question | Was this land forest on **31 Dec 2020**, and has any of it been cleared since? |
| Positioning | Screening + evidence tool, **not** legal certification |
| Pitch framing | You may still say “Clearcut” on stage if preferred; UI brand string is **Groundtruth** unless the team renames in hour 0 |
| Government story | Optional one-liner in the pitch (“same pack a regulator could read”); **do not** rebuild the README’s plantation-approval workflow in 18h |

**Why:** the context doc is the complete build plan (input modes, verdict tiers, Perceptron A+B, evidence pack). The README describes a narrower government check with one hardcoded plot — useful for demo plot inspiration, not as the product spec.

Footer on every page: *Screening tool based on public satellite data. Not a legal certification.*

### EUDR facts for copy (keep accurate)

- Cutoff: **31 Dec 2020**
- Commodities: cattle, cocoa, coffee, oil palm, rubber, soy, wood
- Obligations: **30 Dec 2026** most operators; **30 Jun 2027** certain micro/small
- Plots **> 4 ha** → full polygon; **≤ 4 ha** may be a point
- Forest (FAO): > 0.5 ha, trees > 5 m, canopy > 10%

---

## 2. Remaining questions — resolved defaults

These were open or ambiguous in the context doc. **Treat as decided** unless the team overrides in the first 30 minutes.

| # | Open item | Decision |
| --- | --- | --- |
| Q1 | Leaflet vs Mapbox for draw | **Leaflet + leaflet-draw** (no Mapbox token) |
| Q2 | PDF engine | **WeasyPrint** from an HTML template (faster to style than ReportLab) |
| Q3 | Audit persistence | **Filesystem JSON** under `backend/cache/audits/{id}/` for hackathon (no Postgres) |
| Q4 | Share link | `https://<host>/a/{id}` → `GET /audit/{id}`; IDs are short ulids/nanoids |
| Q5 | Auth | **None** for the hackathon |
| Q6 | Earth Engine | **Optional fallback only**; primary path is GFW Data API + Planetary Computer STAC |
| Q7 | Hansen canopy threshold | **10%** (FAO-aligned); show in evidence pack |
| Q8 | Non-compliant threshold | **≥ 0.5 ha** post-cutoff loss on cutoff-forest inside plot |
| Q9 | Edge band | **100 m**; loss only in edge band → Review |
| Q10 | Point + area mode | Buffer circle from hectares; flag `geometry_approximate: true` everywhere |
| Q11 | Perceptron role | **Explanation + dating only**; never source of hectare numbers |
| Q12 | Demo reliability | Stage demo uses `DEMO_MODE=true` cached audits; live pipeline must work for ≥1 plot |
| Q13 | Map loss coloring | ≤2020 grey; ≥2021 red shades by year |
| Q14 | Cut order if behind | Drop: share link polish → Perceptron B → map overlays. **Never cut:** verdict reasons, before/after slider, evidence pack |

### Still need a human (not blockers for scaffolding)

Do these in hour 0; coding can start without them:

1. **API keys in a shared 1Password / sealed `.env`:** `GFW_API_KEY`, `PERCEPTRON_API_KEY`, Planetary Computer (usually anonymous), optional EE.
2. **Brand string:** “Groundtruth” vs “Clearcut” on the hero (one vote, 2 minutes).
3. **Demo plot picks:** browse [Global Forest Watch](https://www.globalforestwatch.org/) for 3–4 obvious clearings (see §5). The Kalbar plot in the repo README (−0.78, 110.08) is a strong Non-compliant candidate.

---

## 3. Architecture & ownership

```
frontend/          Partner C — Next.js App Router + TS + Tailwind
backend/
  main.py          Partner A owns routes; B/C add handlers behind same app
  geometry.py      A
  gfw_client.py    A
  baseline.py      A
  verdict.py       A  (pure, unit-tested)
  sentinel.py      B
  perceptron_client.py  B
  evidence_pack.py B
  cache/           B owns demo fixtures; A writes live audit JSON
```

Env (never commit): `GFW_API_KEY`, `PERCEPTRON_API_KEY`, `DEMO_MODE`, optional EE creds.

### Routes

| Method | Path | Owner | Notes |
| --- | --- | --- | --- |
| `POST` | `/audit` | A (+ B hooks) | Accept geometry; return `{id}` then process (sync OK for hackathon) |
| `GET` | `/audit/{id}` | A | Full audit record JSON (schema below) |
| `GET` | `/audit/{id}/pdf` | B | Evidence pack |
| `GET` | `/audit/{id}/export` | B | GeoJSON + JSON audit |
| `GET` | `/audit/{id}/assets/{name}` | B | before.png, after.png, timelapse.mp4, map overlays |

---

## 4. Shared audit record (contract)

Partner A publishes this shape first (even with stub zeros). B and C must not invent parallel schemas.

```json
{
  "id": "01J...",
  "created_at": "ISO-8601",
  "plot": {
    "name": "optional string",
    "geometry": { "type": "Polygon", "coordinates": [] },
    "crs": "EPSG:4326",
    "area_ha": 12.3,
    "input_method": "draw|upload|point_area",
    "geometry_approximate": false
  },
  "verdict": {
    "tier": "compliant|review|non_compliant",
    "summary": "one plain-language sentence",
    "reasons": ["human-readable", "..."]
  },
  "baseline": {
    "jrc_forest_ha": 0,
    "hansen_forest_ha": 0,
    "canopy_threshold_pct": 10,
    "disagreement_ha": 0,
    "dataset_versions": { "jrc": "...", "hansen": "..." }
  },
  "loss": {
    "post_cutoff_forest_loss_ha": 0,
    "by_year": { "2021": 0, "2022": 0 },
    "edge_band_100m_ha": 0,
    "alerts": [],
    "dataset_versions": { "umd_tree_cover_loss": "...", "gfw_integrated_alerts": "..." }
  },
  "imagery": {
    "before": { "url": "/audit/{id}/assets/before.png", "date_range": ["2020-10-01", "2020-12-31"], "cloud_pct": 5 },
    "after": { "url": "/audit/{id}/assets/after.png", "date_range": ["...", "..."], "cloud_pct": 8 },
    "timelapse_url": "/audit/{id}/assets/timelapse.mp4"
  },
  "perceptron": {
    "change_detected": true,
    "driver": "plantation|pasture|cropland|road|mining|fire|settlement|none|unclear",
    "description": "...",
    "confidence": "low|medium|high",
    "regions": [{ "box": [0, 0, 1, 1], "label": "..." }],
    "clearing_quarter": "2022-Q3",
    "disagrees_with_numbers": false
  },
  "query_date": "ISO-8601",
  "disclaimer": "Screening tool based on public satellite data. Not a legal certification."
}
```

**Verdict tiers** (implement in `backend/verdict.py`, config object for thresholds):

- **Non-compliant:** ≥ 0.5 ha post-cutoff loss on cutoff-forest inside plot, confirmed by Hansen loss or high-confidence alert.
- **Review:** loss > 0 but < 0.5 ha; edge-only loss; JRC/Hansen disagree; Hansen vs alerts disagree; Perceptron vs numbers disagree; too cloudy; approximate geometry with nearby loss.
- **Compliant:** no post-cutoff loss on cutoff-forest; sources agree.

---

## 5. Demo mode

`DEMO_MODE=true` serves precomputed audits for:

| Slot | Tier | Intent |
| --- | --- | --- |
| 1 | Compliant | Clean plot, sources agree |
| 2 | Non-compliant | Clear post-2021 clearing (plantation/pasture) — Kalbar candidate |
| 3 | Review | Edge loss or JRC/Hansen disagreement |
| 4 (optional) | Compliant | Pre-2020 clearing only (proves we respect cutoff) |

Stage demo **never** depends on live GFW/Perceptron/STAC. Live path still required for ≥1 ad-hoc plot.

---

## 6. Build order (whole team)

1. Hour 0: keys, brand string, clone, `POST /audit` stub returning contract JSON
2. A: geometry → GFW loss → verdict e2e on one plot
3. C: land input + verdict page wired to stub, then live
4. B: before/after composites → slider assets; Perceptron A; PDF
5. Parallel polish: map overlays (C), timeline (C), Perceptron B (B), share link (C)
6. Freeze demo cache 2h before stage

### Integration checkpoints

| When | Check |
| --- | --- |
| +2h | Stub `GET /audit/{id}` matches schema; C renders it |
| +6h | Live numbers + verdict for one drawn polygon |
| +10h | Before/after slider with real Sentinel chips |
| +14h | PDF + demo cache; Perceptron A on loss plots |
| +16h | Cut list applied; stage path is demo-only |

---

## 7. Working conventions

- Verify GFW dataset IDs, field names, and Perceptron request formats against **live docs** before hardcoding.
- Every number in the UI traces to a field stored on the audit record.
- Unit tests required: `verdict.py`, `geometry.py`.
- Small, frequent commits; Partner A owns `backend/verdict.py` and the schema — PRs that change the contract need a Slack/Discord ping.
- Claims stay honest: say what the data shows and its limits.
