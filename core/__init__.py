"""Application core: event bus, WebSocket fan-out, and the FastAPI factory.

Nothing in ``core`` knows what the domain does. It provides the plumbing —
pub/sub, realtime push, HTTP wiring — that ``services`` plugs into.
"""
