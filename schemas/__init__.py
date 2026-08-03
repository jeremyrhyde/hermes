"""Pydantic models shared across the application.

Schemas are the contract between layers: the API validates requests and
serializes responses with them, the event bus carries them, and the web UI
consumes their JSON form. Keep them free of behavior — no I/O, no imports
from ``core`` or ``services`` — so any module can depend on them without
creating a cycle.
"""
