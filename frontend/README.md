# Clearcut frontend (Next.js + shadcn)

React/TypeScript UI for Clearcut intake controls. The live map still runs from `../web/`; this app holds the shadcn components.

## Stack

- Next.js App Router + TypeScript
- Tailwind CSS
- shadcn-style structure: `src/components/ui`, `src/lib/utils.ts`, `components.json`

Default component path is **`src/components/ui`** (with `@/components/ui` alias). Keep UI primitives there so shadcn CLI adds and updates components consistently. If you only use `/components/ui` at the repo root, aliases and CLI paths will drift—prefer `src/components/ui` in this Next app.

## Setup (already done in this folder)

```bash
cd frontend
npm install
npm run dev
# http://localhost:3000
```

Dependencies used by the toggles: `next-themes`, `lucide-react`, `framer-motion`, `clsx`, `tailwind-merge`.

## Components

- `src/components/ui/mode-toggle.tsx` — box / shape / upload segmented control
- `src/components/ui/toggle-theme.tsx` — system / light / dark (as provided)
- Map tools in `../web/` mirror the same three intake modes

## Fresh machine from scratch

```bash
npx create-next-app@latest frontend --typescript --tailwind --eslint --app --src-dir --import-alias "@/*"
cd frontend
npx shadcn@latest init
npm install next-themes lucide-react framer-motion
# copy components into src/components/ui
```
