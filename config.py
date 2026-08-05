"""Application configuration loaded from environment or `.env`.

Uses `pydantic-settings`. Field names are uppercase to match the convention for
environment variables — e.g. setting `PORT=9000` in the environment overrides
the default. A `.env` file in the project root is also honored automatically.

Example:
    >>> from config import Settings
    >>> s = Settings()
    >>> s.PORT
    8000
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from schemas.source import SourcesConfig


class Settings(BaseSettings):
    """Runtime settings for the Hermes server.

    Fields:
        HOST: Address the FastAPI server binds to. Default ``"0.0.0.0"``.
        PORT: TCP port the server listens on. Default ``8000``.
        LOG_LEVEL: Log level passed to uvicorn / loggers. Default ``"info"``.
        WEB_DIR: Directory containing the static frontend served by FastAPI
            under ``/ui``. Default ``"./web"``.
        DB_PATH: Path to the SQLite database file. Default ``"./hermes.db"``.
        SOURCES_CONFIG_PATH: Path to the declarative source list. A missing
            file yields an empty source list rather than an error, so the
            server boots unconfigured. Default ``"./sources.yaml"``.
        ANTHROPIC_API_KEY: Key used for summarization. Empty by default; if
            unset, the server still boots but the poller never starts, so
            nothing is ingested or summarized, and the failure is reported
            at ``/health``.
        SUMMARY_MODEL: Model used for summarization. Default
            ``"claude-haiku-4-5"``.
        SUMMARY_MAX_INPUT_CHARS: Articles longer than this are truncated
            before the API call, bounding cost on outlier posts (~60k chars
            ≈ 15k tokens). Default ``60_000``.
        POLL_TICK_SECONDS: How often the poller loop wakes. Default ``60.0``.
        POLL_MIN_SECONDS: Lower clamp on each source's adaptive poll
            interval. Default ``900`` (15 min).
        POLL_MAX_SECONDS: Upper clamp on each source's adaptive poll
            interval. Default ``14_400`` (4 h).
        DEFAULT_SCORE_CUTOFF: Seed value only, used to populate the
            ``preferences`` table on first run; the live knob is
            runtime-editable from the UI thereafter. Default ``70``.
        DEFAULT_MAX_DISPLAYED: Seed value only, used to populate the
            ``preferences`` table on first run; the live knob is
            runtime-editable from the UI thereafter. Default ``5``.

    Add domain settings (DB path, upstream URLs, credentials, poll intervals)
    as new fields here rather than reading ``os.environ`` at the call site —
    that keeps every knob discoverable in one place and documented in
    ``.env.example``.
    """

    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "info"
    WEB_DIR: str = "./web"

    DB_PATH: str = "./hermes.db"
    SOURCES_CONFIG_PATH: str = "./sources.yaml"

    ANTHROPIC_API_KEY: str = ""
    SUMMARY_MODEL: str = "claude-haiku-4-5"
    SUMMARY_MAX_INPUT_CHARS: int = 60_000

    POLL_TICK_SECONDS: float = Field(default=60.0, gt=0)
    POLL_MIN_SECONDS: int = Field(default=900, gt=0)
    POLL_MAX_SECONDS: int = Field(default=14_400, gt=0)

    DEFAULT_SCORE_CUTOFF: int = 70
    DEFAULT_MAX_DISPLAYED: int = 5

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


def _normalize_categories(names: list[str]) -> list[str]:
    """Lowercase, de-duplicate, and preserve order."""

    seen: set[str] = set()
    normalized = []
    for name in names:
        lowered = name.lower()
        if lowered not in seen:
            seen.add(lowered)
            normalized.append(lowered)
    return normalized


def load_sources_config(path: str | Path | None = None) -> SourcesConfig:
    """Read and validate ``sources.yaml`` at *path*.

    Returns an empty config if the file does not exist, so the server boots
    before any sources are configured.

    Category names in the ``categories`` block are normalized here — this is
    the one place that does it, so every downstream consumer (tool schema,
    query validator, UI) sees the same canonical form. Raises ``ValueError``
    naming any ``filters`` entry that has no corresponding ``vocabulary``
    entry, since such a filter could never match anything.
    """

    if path is None:
        path = Settings().SOURCES_CONFIG_PATH
    p = Path(path)
    if not p.exists():
        return SourcesConfig(sources=[])

    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    config = SourcesConfig.model_validate(raw)

    vocabulary = _normalize_categories(config.categories.vocabulary)
    filters = _normalize_categories(config.categories.filters)
    unknown = [f for f in filters if f not in vocabulary]
    if unknown:
        raise ValueError(
            f"categories.filters contains entries not in categories.vocabulary: "
            f"{', '.join(unknown)}"
        )
    config.categories.vocabulary = vocabulary
    config.categories.filters = filters

    return config
