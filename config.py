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

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the Hermes server.

    Fields:
        HOST: Address the FastAPI server binds to. Default ``"0.0.0.0"``.
        PORT: TCP port the server listens on. Default ``8000``.
        LOG_LEVEL: Log level passed to uvicorn / loggers. Default ``"info"``.
        WEB_DIR: Directory containing the static frontend served by FastAPI
            under ``/ui``. Default ``"./web"``.

    Add domain settings (DB path, upstream URLs, credentials, poll intervals)
    as new fields here rather than reading ``os.environ`` at the call site —
    that keeps every knob discoverable in one place and documented in
    ``.env.example``.
    """

    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "info"
    WEB_DIR: str = "./web"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
