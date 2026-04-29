"""Unit tests for Settings — defaults and env-var overrides."""

from __future__ import annotations

import pytest

from google_ads_mcp.settings import Settings


def test_defaults_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    # Strip any env that would shadow the defaults.
    for key in list(__import__("os").environ):
        if key.startswith("GOOGLE_ADS_MCP_"):
            monkeypatch.delenv(key, raising=False)

    s = Settings()

    assert s.gaql_max_rows == 1000
    assert s.gaql_max_response_bytes == 256_000
    assert s.log_level == "INFO"
    # Path defaults end in the project's known filenames; we don't assert the
    # full path because XDG vars on the host vary.
    assert s.credentials_path.name == "credentials.yaml"
    assert s.audit_log_path.name == "audit.log"


def test_env_var_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_ADS_MCP_GAQL_MAX_ROWS", "42")
    monkeypatch.setenv("GOOGLE_ADS_MCP_LOG_LEVEL", "DEBUG")

    s = Settings()

    assert s.gaql_max_rows == 42
    assert s.log_level == "DEBUG"


def test_invalid_max_rows_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(gaql_max_rows=0)


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(log_level="TRACE")  # pyright: ignore[reportArgumentType]
