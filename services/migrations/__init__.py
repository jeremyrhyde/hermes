"""Forward-only SQLite migration runner.

Keyed on ``PRAGMA user_version``. Migration files are named ``NNN_name.sql``
and applied in numeric order; a file whose number is <= the current version is
skipped. There is no down-migration — to undo, write a new forward migration.

Never edit a migration that has run anywhere. Add a new one.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent


def _version_of(path: Path) -> int:
    """Parse the leading integer from ``001_initial.sql``."""

    return int(path.name.split("_", 1)[0])


async def apply_migrations(
    db: aiosqlite.Connection, migrations_dir: Path = MIGRATIONS_DIR
) -> int:
    """Apply every pending migration in order. Returns the resulting version."""

    cur = await db.execute("PRAGMA user_version")
    row = await cur.fetchone()
    current = int(row[0]) if row else 0

    pending = sorted(
        (p for p in migrations_dir.glob("*.sql") if _version_of(p) > current),
        key=_version_of,
    )

    for path in pending:
        version = _version_of(path)
        logger.info("migrations: applying %s", path.name)
        await db.executescript(path.read_text(encoding="utf-8"))
        # executescript commits and ends any open transaction, so the pragma
        # must be a separate statement afterwards. version is parsed from the
        # filename via int() above, so it cannot carry injected SQL — safe to
        # interpolate; PRAGMA does not accept bound parameters anyway.
        await db.execute(f"PRAGMA user_version = {version}")
        await db.commit()
        current = version

    return current
