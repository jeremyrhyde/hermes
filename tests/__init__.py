"""Test suite.

Tests build a hermetic app via ``core.api.create_app(..., mount_static=False)``
rather than importing ``main.app`` — that keeps them free of the real
lifespan, the filesystem, and any network I/O.
"""
