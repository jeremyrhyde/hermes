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
        PROFILE_PATH: Path to the hand-written taste profile seeded into
            ``profile_versions`` on first run. Gitignored, like
            ``sources.yaml``. A missing file disables scoring and is reported
            at ``/health``; articles still ingest and summarize. Default
            ``"./profile.md"``.
        ANTHROPIC_API_KEY: Key used for summarization. Empty by default; if
            unset, the server still boots but the poller never starts, so
            nothing is ingested or summarized, and the failure is reported
            at ``/health``.
        SUMMARY_MODEL: Model used for summarization. Default
            ``"claude-haiku-4-5"``.
        SUMMARY_MAX_INPUT_CHARS: Articles longer than this are truncated
            before the API call, bounding cost on outlier posts (~60k chars
            ≈ 15k tokens). Default ``60_000``.
        SCORE_MODEL: Model used for scoring. A larger model than
            ``SUMMARY_MODEL`` on purpose: scoring is a judgment call against a
            rubric, not the grounded extraction that R7 found small models
            handle well. Default ``"claude-sonnet-5"``.
        POLL_TICK_SECONDS: How often the poller loop wakes. Default ``60.0``.
        POLL_MIN_SECONDS: Lower clamp on each source's adaptive poll
            interval. Default ``900`` (15 min).
        POLL_MAX_SECONDS: Upper clamp on each source's adaptive poll
            interval. Default ``14_400`` (4 h).
        DEFAULT_SCORE_CUTOFF: Seed value only, used to populate the
            ``preferences`` table on first run; the live knob is
            runtime-editable from the UI thereafter. Default ``0`` — scoring
            ships in calibration mode, where nothing is hidden until the scores
            have been read against real articles.
        DEFAULT_MAX_DISPLAYED: Seed value only, used to populate the
            ``preferences`` table on first run; the live knob is
            runtime-editable from the UI thereafter. Default ``50``.
        DEFAULT_DISTILL_THRESHOLD: Seed value only, like the two above. How
            many ratings accumulate before the reader is offered a fresh
            profile proposal. Default ``20`` — enough ratings that a proposal
            has something to generalize from, few enough that the loop closes
            within a week of ordinary reading.

    The three ``DEFAULT_`` values are mirrored by ``core.api._PREFERENCES``, which
    supplies them when no row exists at all. Change one and change the other.

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
    PROFILE_PATH: str = "./profile.md"

    ANTHROPIC_API_KEY: str = ""
    SUMMARY_MODEL: str = "claude-haiku-4-5"
    SUMMARY_MAX_INPUT_CHARS: int = 60_000
    SCORE_MODEL: str = "claude-sonnet-5"

    POLL_TICK_SECONDS: float = Field(default=60.0, gt=0)
    POLL_MIN_SECONDS: int = Field(default=900, gt=0)
    POLL_MAX_SECONDS: int = Field(default=14_400, gt=0)

    DEFAULT_SCORE_CUTOFF: int = 0
    DEFAULT_MAX_DISPLAYED: int = 50
    DEFAULT_DISTILL_THRESHOLD: int = 20

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


class CategoryConfigError(ValueError):
    """Raised when categories.filters is not a subset of categories.vocabulary.

    Carries the otherwise-valid config so a caller can boot with the real
    source list and categories disabled. Subclasses ``ValueError`` so callers
    that only care that the config was rejected need no special case.
    """

    def __init__(self, message: str, config: SourcesConfig) -> None:
        super().__init__(message)
        self.config = config


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
    entry, since such a filter could never match anything. The raised
    :class:`CategoryConfigError` carries the parsed config so a caller can
    still boot with the real ``sources`` list; its ``categories`` block is the
    rejected, un-normalized one and must not be used.
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
        raise CategoryConfigError(
            f"categories.filters contains entries not in categories.vocabulary: "
            f"{', '.join(unknown)}",
            config,
        )
    config.categories.vocabulary = vocabulary
    config.categories.filters = filters

    return config
