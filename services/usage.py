"""Count Claude API calls for Pantheon's status screen.

`ClaudeUsage.wrap(client)` returns the Anthropic client with
`messages.create` recorded — main.py wraps the one shared client, so the
summarizer, scorer and distiller are unchanged. Counts cover the last 24 h
and live in memory (they reset when Hermes restarts).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Any

_WINDOW_S = 86_400.0


class ClaudeUsage:
    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._calls: deque[float] = deque()
        self._errors: deque[float] = deque()
        self.last_call: float | None = None

    def record(self, ok: bool) -> None:
        now = self._clock()
        self._calls.append(now)
        self.last_call = now
        if not ok:
            self._errors.append(now)

    def counts(self) -> tuple[int, int]:
        cutoff = self._clock() - _WINDOW_S
        for times in (self._calls, self._errors):
            while times and times[0] < cutoff:
                times.popleft()
        return len(self._calls), len(self._errors)

    def wrap(self, client: Any) -> Any:
        return _RecordingClient(client, self)


class _RecordingMessages:
    def __init__(self, inner: Any, usage: ClaudeUsage) -> None:
        self._inner = inner
        self._usage = usage

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        try:
            response = await self._inner.create(*args, **kwargs)
        except Exception:
            self._usage.record(False)
            raise
        self._usage.record(True)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RecordingClient:
    def __init__(self, inner: Any, usage: ClaudeUsage) -> None:
        self._inner = inner
        self.messages = _RecordingMessages(inner.messages, usage)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
