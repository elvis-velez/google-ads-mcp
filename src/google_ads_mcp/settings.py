"""Server settings.

Loaded from environment variables prefixed `GOOGLE_ADS_MCP_` over
compiled-in defaults. Validated at construction time so bad config fails
fast — before any tool registers.

Tests construct `Settings(...)` directly with explicit kwargs; production
calls `Settings()` and lets the env layer override defaults.

Paths follow XDG conventions, so Arch / Linux users get the canonical
locations and `XDG_CONFIG_HOME` / `XDG_DATA_HOME` overrides are respected.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


def _xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def _xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))


def _default_credentials_path() -> Path:
    return _xdg_config_home() / "google-ads-mcp" / "credentials.yaml"


def _default_audit_log_path() -> Path:
    return _xdg_data_home() / "google-ads-mcp" / "audit.log"


class Settings(BaseSettings):
    """Top-level server settings.

    Knobs are intentionally minimal in v1. New ones get added when a real
    workflow needs them, not on speculation.
    """

    model_config = SettingsConfigDict(env_prefix="GOOGLE_ADS_MCP_")

    credentials_path: Path = Field(default_factory=_default_credentials_path)
    audit_log_path: Path = Field(default_factory=_default_audit_log_path)

    # Hard caps on what a single GAQL call can return to the LLM. The byte
    # cap protects context window; the row cap protects against pathological
    # queries that ask for "all keywords" on a 50k-keyword account. When
    # either cap fires, the response is truncated and a reason is reported.
    gaql_max_rows: int = Field(default=1000, gt=0, le=100_000)
    gaql_max_response_bytes: int = Field(default=256_000, gt=0)

    # Mutate-path safety thresholds. Override per-op via Operation.force_override
    # for CPC and budget; batch size and customer-allowlist are not overridable.
    # Values in micros (1 USD = 1_000_000 micros).
    cpc_max_micros: int = Field(default=50_000_000, gt=0)         # $50.00
    budget_max_daily_micros: int = Field(default=1_000_000_000, gt=0)  # $1000.00
    mutate_max_ops_per_call: int = Field(default=100, gt=0, le=10_000)
    mutate_id_ttl_seconds: int = Field(default=900, gt=0)         # 15 minutes

    log_level: LogLevel = "INFO"
