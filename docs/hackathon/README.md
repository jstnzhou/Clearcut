# Groundtruth / Clearcut — DubHacks 2026 partner split

Three parallel tracks for an ~18h build. Read **shared** first, then only your partner brief.

| Doc | Owner | Owns |
| --- | --- | --- |
| [`00-shared.md`](./00-shared.md) | Everyone | Product lock, resolved questions, API contract, cut order, demo plots |
| [`partner-a-geospatial.md`](./partner-a-geospatial.md) | Partner A | Geometry, GFW/baseline numbers, verdict |
| [`partner-b-imagery-ai.md`](./partner-b-imagery-ai.md) | Partner B | Sentinel-2, Perceptron, evidence pack, demo cache |
| [`partner-c-frontend.md`](./partner-c-frontend.md) | Partner C | Next.js UI, map input, audit page, share link |

**Integration rule:** Partner A defines the audit record schema. Partners B and C consume it. Merge through `POST /audit` → `GET /audit/{id}` as soon as A has a stub response.

Source brief: uploaded `clearcut-cursor-context.md` (Groundtruth hackathon context). Repo `README.md` still describes an earlier government-facing Clearcut framing — see shared doc §1 for the lock.
