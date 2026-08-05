# Hermes

A FastAPI core that serves a no-build web UI, wired together by an in-process
event bus with WebSocket push to the browser.

The server boots, polls sources on an adaptive schedule, summarizes new
articles via Claude, and serves a live web UI over the event bus described
above.

## What this is becoming

A single-user personal feed: pull articles from Substack and other sources,
summarize each into a headline plus five bullets via Claude, and surface a
small gated set ranked against a taste profile that learns from ± feedback.

**Design documents — read these before writing feed code. Load all three
together; each is lossy without the others.**

| Document | Contents |
|---|---|
| `2026-08-02-personal-feed-spec.md` | Approved design: goals, schema, interfaces, pipeline, four-phase plan with exit gates |
| `2026-08-02-feed-research-context.md` | Prior-art research: verified findings with sources, **seven refuted claims not to reintroduce**, and what the research failed to answer |
| `2026-08-02-personal-feed-phase1.md` | Task-by-task TDD implementation plan for phase 1 |

None are committed to git. Research findings are cited throughout as `[R#]`,
consistent across all three.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/).

```bash
make install     # uv sync — creates .venv from pyproject.toml
make run         # start the server
```

Then open <http://localhost:8000/ui/>.

Bare `make` prints the full target list. `make run-dev` starts with
auto-reload; `make test` runs the suite.

With the server up and the UI open, from a second terminal:

```bash
make health      # GET  /health
make sources     # configured sources + their poll health
make feed        # 10 most recent summarized articles
make poll-now ID=astralcodexten   # force one source to poll immediately
```

## Setup

1. `cp sources.yaml.example sources.yaml` and list the feeds you follow.
2. `cp .env.example .env` and set `ANTHROPIC_API_KEY`. Without it the server
   still boots, but the poller never starts — nothing is ingested or
   summarized, `POST /sources/{id}/poll` (and `make poll-now`) returns 503,
   and `/health` reports the startup failure.
3. `make run`, then open <http://localhost:8000/ui/>.

The poller wakes every 60s and polls each source on its own adaptive schedule
(15 min–4 h). To see something immediately, use `make poll-now ID=<source-id>`.

## Layout

| Path | Owns |
|------|------|
| `main.py` | Entry point: builds the app, owns the startup/shutdown lifespan, exposes `/health` |
| `config.py` | `Settings` — every env-var knob in one pydantic-settings class |
| `core/api.py` | FastAPI factory: routers, the `/ws` endpoint, the `/ui` static mount |
| `core/events.py` | `EventBus` — async pub/sub, the seam between components |
| `core/websocket.py` | `WebSocketManager` — connection set + broadcast fan-out |
| `schemas/` | Pydantic models. No I/O, no imports from `core`/`services` |
| `services/` | Feed domain: source drivers, extraction, summarizer, pipeline, poller |
| `web/` | The UI: `index.html`, `style.css`, `app.js`. No build step |
| `tests/` | pytest suite against a hermetic app |

## Architecture

### Factory + lifespan

`core.api.create_app()` takes every component as a keyword-only argument and
returns a wired `FastAPI`. It never constructs its own dependencies, so tests
can pass mocks and skip the static mount.

`main.build_app()` calls that factory, then attaches an async lifespan that
does all the real I/O — component construction, background task startup — on
boot, and tears down in reverse order on shutdown. Importing `main` performs
no I/O, which is what lets `uvicorn main:app` and `python main.py` share one
code path.

Components are stashed on `app.state` and read back in endpoints through
small helpers (`_bus(request)`), instead of `Depends` boilerplate.

Startup failures are collected, not raised: a component that fails to build
gets appended to a failure list surfaced at `/health` and rendered as a
banner in the UI, and the server stays up.

### Event flow

```
   page load
       │
       ▼
   refreshAll()  ──►  GET /health                (Promise.allSettled)
       │
       ▼
   connectWebSocket()  ──►  ws://host/ws         (exponential backoff, 1s → 30s)
       │
       ▼
   user acts                              something publishes an Event
       │                                              │
       ▼                                              ▼
   optimistic flip  ──► POST /...            EventBus.publish()
       │                    │                         │
       ▼                    ▼                         ▼
   re-sync from response   ...            WebSocketManager.broadcast()
                                                      │
                                                      ▼
                                              applyEvent(event) in app.js
```

A producer publishes an `Event` without knowing who listens. The
`WebSocketManager` subscribes itself to the bus at startup, so anything
published reaches every connected browser without the API layer broadcasting
manually. Both the bus and the broadcast fan out with
`asyncio.gather(..., return_exceptions=True)` — one failing subscriber or one
dead socket can never block the rest.

### No build step

The UI is vanilla HTML + CSS + [Alpine.js](https://alpinejs.dev) from a CDN.
Edit a file in `web/`, hard-refresh the browser, see the change. No `npm
install`, no bundler, no source maps. `app.js` exports a single `app()`
factory consumed by `x-data="app()"` on `<body>`; all UI state lives on that
one object. All colors and spacing are CSS custom properties on `:root` in
`style.css` — rebrand by overriding tokens, not by editing component rules.

`/ui/` and the API are served from one process, same origin, so there's no
CORS story to configure.

## Extending it

**A new endpoint** — add a `_build_<name>_router()` factory in `core/api.py`
returning an `APIRouter` with a prefix, and `include_router` it in
`create_app`. Read components off `app.state` via a helper.

**A new component** — construct it in `main._build_components`, hand it the
`EventBus`, `await start()` it if it has a lifecycle, assign it onto
`app.state` in the lifespan, and stop it in the `finally` block. Add a
keyword-only parameter for it on `create_app`.

**A new event type** — add it to `EventType` in `schemas/events.py`, list it
in `BROADCAST_TYPES` in `core/websocket.py` if the browser should see it, and
add a `case` to the `switch` in `applyEvent()` in `web/app.js`.

**A new setting** — add a field to `Settings` in `config.py` and document it
in `.env.example`. Don't read `os.environ` at the call site.

**A new tab** — add a `<button>` to `<nav class="tabs">` and a sibling
`<section x-show="tab === '...'">` in `index.html`. No router, no config.

## Configuration

Every setting is optional and has a default. Copy `.env.example` to `.env`
and uncomment what you need, or set the variables in the environment.

| Variable | Default | Meaning |
|----------|---------|---------|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Listen port |
| `LOG_LEVEL` | `info` | Root log level |
| `WEB_DIR` | `./web` | Static UI directory mounted at `/ui` |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| **404 at `/ui/`** | `WEB_DIR` missing, so the mount was skipped | Confirm `web/index.html` exists; look for `"Static UI mounted at /ui"` in the log |
| **`/ui` (no slash) 404s** | `StaticFiles` only serves the trailing-slash form | Always link `/ui/` |
| **No live updates** | WebSocket never connected | DevTools → Network → WS; the `/ws` row should be 101. The header dot is red when offline |
| **WS connects then drops** | A reverse proxy stripping `Upgrade` headers | Bypass the proxy in dev, or forward `Upgrade` and `Connection` |
| **Raw `x-text` flashes on load** | Alpine hasn't initialized | The `[x-cloak]` rule covers this; confirm `style.css` loaded |
