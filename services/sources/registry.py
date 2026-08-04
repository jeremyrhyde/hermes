"""Driver registry — maps ``SourceConfig.type`` to a driver instance.

Mirrors Hestia's DriverRegistry. Drivers are stateless with respect to a given
source, so one instance serves every source of that type.
"""

from __future__ import annotations

import logging

from schemas.source import SourceConfig
from services.sources.base import SourceDriver

logger = logging.getLogger(__name__)


class SourceRegistry:
    def __init__(self) -> None:
        self._drivers: dict[str, SourceDriver] = {}

    def register(self, driver: SourceDriver) -> None:
        self._drivers[driver.source_type] = driver
        logger.info("registry: registered driver for type %r", driver.source_type)

    def for_source(self, source: SourceConfig) -> SourceDriver:
        try:
            return self._drivers[source.type]
        except KeyError:
            raise KeyError(
                f"no driver registered for source type {source.type!r} "
                f"(known: {sorted(self._drivers)})"
            ) from None

    def known_types(self) -> list[str]:
        return sorted(self._drivers)
