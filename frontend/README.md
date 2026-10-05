# Hermes UI

Svelte 5 (runes only) + Vite + TypeScript, laid out like Apollo's frontend.

```bash
npm ci            # once
npm run dev       # :5173, proxies /api (incl. the WebSocket) and /health to :8002
npm test          # vitest
npm run check     # svelte-check + tsc
npm run build     # → dist/, served by FastAPI at /
```

The proxy target is `http://localhost:$HERMES_PORT` (default 8002). From the
repo root, `make run-dev` starts the API and this dev server together, and
`make run-dev PORT=9000` moves both. `make test` runs pytest, vitest and
`npm run check`.

## Layout

- `src/lib/*.ts` — pure logic with tests beside it (`route`, `feed`, `swipe`,
  `live`, `prefs`, `format`, `settings`, `api`, `motion`, `types`). Put anything worth a test here.
- `src/lib/*.svelte.ts` — `$state` stores wrapping that logic (`feed`,
  `settings`, `app`, `router`, `live`, `prefs`, `toast`).
- `src/components/` — shared pieces (NavPill, Header, FeedCard, …).
- `src/screens/` — one component per screen (Feed and Saved share `FeedScreen`); Settings (knobs, taste profile,
  review, Display, About) is split into `src/screens/settings/`.
- `src/styles/tokens.css` — design tokens; `src/app.css` — global rules.

## Rules

- Every fetch goes through `src/lib/api.ts`. URLs are relative — no leading
  `/` — so one build works at `:8002/` and under Pantheon's `/hermes/`
  (`tests/test_contract.py` enforces this).
- No hex values outside `src/styles/tokens.css`.
- Tap targets ≥ `var(--tap-min)`; inputs ≥ 16px.
- Every duration goes through `dur()` (`src/lib/motion.ts`); animate only
  `transform` and `opacity`; no `backdrop-filter` (Pi kiosks).
- Theme and motion are always resolved onto `<html>` as `data-theme` and
  `data-motion` by `prefs.svelte.ts`, so CSS has one light block and one
  reduced-motion rule.

## Adding a screen

1. Add the tab to `TABS` in `src/lib/route.ts` (and its tests).
2. Add an entry to `ITEMS` in `src/components/NavPill.svelte` (and widen the
   grid's column count).
3. Add a branch in `App.svelte`'s `{#key tab}` block.

## Icons

`src/components/Icon.svelte` holds inline Lucide paths; add one there. The app
icon is `public/icon.svg`; after editing it run `frontend/scripts/icons.sh` and commit
the PNGs.
