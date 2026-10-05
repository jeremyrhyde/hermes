# `core/` — application plumbing

Generic infrastructure with no knowledge of what Hermes actually does. If a
module in here starts needing domain vocabulary, it belongs in `services/`
instead.

| File | Owns |
|------|------|
| `api.py` | The `create_app()` factory: routers, `/api/ws`, the UI mounted at `/` |
| `events.py` | `EventBus` — async pub/sub keyed by `EventType` |
| `websocket.py` | `WebSocketManager` — connection set, broadcast fan-out, bus subscription |

## Wiring order

`main._build_components` constructs these in dependency order:

1. `EventBus()` — no dependencies.
2. Domain services — each handed the bus so it can publish.
3. `WebSocketManager()` + `subscribe_to_bus(bus)` — must come after anything
   that publishes during startup if those events should reach the browser.
4. `create_app(...)` — receives the finished components.

Teardown runs in reverse, each step wrapped so one failure doesn't skip the
rest.

## Conventions

**Injection, not import.** Nothing in `core` reaches for a global. Components
arrive as keyword-only arguments to `create_app` and are stashed on
`app.state`; endpoints read them back through the module-level helpers
(`_bus(request)`, `_ws_manager(ws)`). There is deliberately no `EventBus`
singleton — construct one per process in the lifespan, and one per test.

**Failure isolation.** Both `EventBus.publish` and
`WebSocketManager.broadcast` fan out with
`asyncio.gather(..., return_exceptions=True)` and log-and-absorb rather than
propagate. One bad subscriber or one dead socket must never take down the
publisher or the other listeners.

**Routers are factories.** Each `_build_*_router()` returns a fresh
`APIRouter` with its own prefix and tags, so `create_app` composes them and
tests can build any subset.
