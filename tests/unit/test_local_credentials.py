"""Unit tests for LocalRefreshTokenCredentials.

Exercises the YAML-on-disk path with `tmp_path`; no real creds touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from google_ads_mcp.auth.local import LocalRefreshTokenCredentials
from google_ads_mcp.errors import ConfigError

VALID_YAML = """\
developer_token: dev-token-abc
oauth_client_id: client-id-xyz
oauth_client_secret: client-secret-123
refresh_token: refresh-456
login_customer_id: '1234567890'
"""

VALID_YAML_NO_MCC = """\
developer_token: dev-token-abc
oauth_client_id: client-id-xyz
oauth_client_secret: client-secret-123
refresh_token: refresh-456
"""


def _write(tmp_path: Path, contents: str) -> Path:
    path = tmp_path / "credentials.yaml"
    path.write_text(contents)
    return path


def test_loads_full_credentials(tmp_path: Path) -> None:
    creds = LocalRefreshTokenCredentials(_write(tmp_path, VALID_YAML)).get()

    assert creds.developer_token == "dev-token-abc"
    assert creds.oauth_client_id == "client-id-xyz"
    assert creds.oauth_client_secret == "client-secret-123"
    assert creds.refresh_token == "refresh-456"
    assert creds.login_customer_id == "1234567890"


def test_login_customer_id_optional(tmp_path: Path) -> None:
    creds = LocalRefreshTokenCredentials(_write(tmp_path, VALID_YAML_NO_MCC)).get()

    assert creds.login_customer_id is None


def test_missing_file_raises(tmp_path: Path) -> None:
    provider = LocalRefreshTokenCredentials(tmp_path / "nope.yaml")

    with pytest.raises(ConfigError, match="not found"):
        provider.get()


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "developer_token: [unterminated")

    with pytest.raises(ConfigError, match="not valid YAML"):
        LocalRefreshTokenCredentials(path).get()


def test_missing_required_field_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "developer_token: only-this\n")

    with pytest.raises(ConfigError, match="missing or malformed"):
        LocalRefreshTokenCredentials(path).get()


def test_non_mapping_yaml_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, "- a list at top level\n")

    with pytest.raises(ConfigError, match="must be a YAML mapping"):
        LocalRefreshTokenCredentials(path).get()


def test_caches_after_first_read(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID_YAML)
    provider = LocalRefreshTokenCredentials(path)

    first = provider.get()
    # Mutating the file after first read should not affect the cached result.
    path.write_text("garbage: but cached so we never re-read")
    second = provider.get()

    assert first is second
