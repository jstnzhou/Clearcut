# Partner C — Frontend & audit experience

**You own the demo moment:** phone-friendly land input and the single audit page that makes judges lean in (before/after slider).

Read [`00-shared.md`](./00-shared.md) first. Consume Partner A’s audit JSON; do not recompute hectares in the browser.

## Mission

Mobile-first web app:

1. Enter a plot (draw / upload / point+area)  
2. Show audit: verdict → **hero before/after slider** → map → timeline → Perceptron copy → actions  
3. Share link + polish for SpaceXAI / Cursor-heavy judging  

## Owns (files)

```
frontend/                    # Next.js App Router + TypeScript + Tailwind
  app/page.tsx               # land input
  app/a/[id]/page.tsx        # audit view (share target)
  components/MapDraw.tsx
  components/BeforeAfterSlider.tsx
  components/AuditMap.tsx
  components/TimelineStrip.tsx
  components/VerdictBanner.tsx
  lib/api.ts                 # typed client for GET/POST /audit
```

Map stack lock: **Leaflet + leaflet-draw** (see shared Q1). Charts: Recharts.

## Does not own

- Verdict math, GFW queries → **A**  
- STAC, Perceptron, PDF generation → **B** (you link to their URLs)

## Hour-by-hour (guide)

| Phase | Deliverable |
| --- | --- |
| 0–1h | Scaffold Next.js + Tailwind; env `NEXT_PUBLIC_API_URL`; typed `Audit` interface matching shared schema |
| 1–3h | Land input v1: draw polygon on satellite basemap; POST geometry; navigate to `/a/{id}` |
| 2–4h | Verdict banner + layout shell reading stub JSON from A |
| 4–7h | **Before/after slider** (hero) — big, smooth, plot outline; this is the stage shot |
| 5–8h | Upload GeoJSON (KML client-side with `@tmcw/togeojson` if time); point+area mode for ≤ 4 ha |
| 8–11h | Map: outline, cutoff-forest shading (when A provides), loss by year (grey ≤2020 / red ≥2021), optional Perceptron boxes |
| 9–12h | Timeline strip: annual loss bars, bold cutoff at 31 Dec 2020, Perceptron clearing marker |
| 12–15h | Actions: open PDF, download export, copy share link; persistent disclaimer footer |
| 14–16h | Demo picker (3–4 cached plots); mobile pass; loading/error states; Cursor-friendly README gif optional |

## UI structure (single audit page, top → bottom)

1. **Verdict banner** — tier color, one-sentence reason, plot name/area  
2. **Before/after slider (hero)** — Sentinel Dec 2020 vs latest; do not bury this  
3. **Map** — geometry + layers above  
4. **Timeline strip** — compact  
5. **Perceptron description** — plain language; label as AI interpretation  
6. **Actions** — Download evidence pack, Export data, Copy share link  

Footer always: *Screening tool based on public satellite data. Not a legal certification.*

## UX constraints (primary user)

- Farmer / coop officer, phone, possibly low bandwidth, not a GIS expert  
- Plain language (“Some trees were cleared here in 2022, after the EU cutoff date”)  
- Large tap targets, minimal jargon  
- Share link is the job-to-be-done for EU buyers  

## Design notes for this hackathon

- One composition on first viewport of marketing/landing if you add one: brand **Groundtruth** (or team-chosen name), one headline, one short line, one CTA into “Audit a plot” — no stat strips  
- Audit page may be denser (tool UI); still keep the slider dominant  
- Avoid generic purple-gradient AI aesthetic; prefer earth/forest imagery and clear tier colors (green / amber / red) used sparingly for verdict only  

## API usage

```ts
POST /audit  body: { name?, geometry, input_method, area_ha? }
→ { id }

GET /audit/{id}
→ Audit record (shared schema)

GET /audit/{id}/pdf      // open or download
GET /audit/{id}/export
GET /audit/{id}/assets/*
```

While A/B are incomplete: mock the `Audit` type locally behind a flag, but switch to real `GET` at the +2h checkpoint.

## Handoffs

**From A:** schema-stable JSON; demo IDs documented.  
**From B:** `imagery.before/after` URLs, timelapse, Perceptron fields.  
**To A/B:** exact payload shape for draw/upload/point_area; report any schema mismatches immediately.

## Definition of done

- [ ] Phone-width draw → audit page works against live backend  
- [ ] Slider is the visual hero with real or demo imagery  
- [ ] Timeline shows cutoff line  
- [ ] Copy share link works (`/a/{id}`)  
- [ ] Demo mode: one-tap load of Compliant / Non-compliant / Review  
- [ ] Disclaimer visible; copy never claims “certified”  

## Cut if behind

Keep: draw (or GeoJSON paste), verdict banner, before/after slider, share route, disclaimer.  
Defer: KML/GPX, fancy map overlays, timeline polish, point+area, landing marketing page.
