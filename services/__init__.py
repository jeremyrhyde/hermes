"""Domain logic lives here.

Deliberately empty — this is the seam where Hermes' actual work plugs into
the generic core. The expected shape, mirroring how ``core`` is built:

- Each service is a class constructed in ``main._build_components`` and
  handed the :class:`core.events.EventBus` so it can publish without knowing
  who listens.
- Anything with a lifecycle exposes ``async def start()`` / ``async def
  close()``; ``main``'s lifespan calls them in order on startup and in
  reverse on shutdown.
- A construction failure should be recorded and surfaced at ``/health``
  rather than aborting startup, so the rest of the system stays usable.
- HTTP surface goes in a ``_build_*_router()`` factory in ``core/api.py``,
  reading the service off ``app.state`` through a module-level helper.
"""
