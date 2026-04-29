"""Unit tests for the pure helpers inside auth.init_cmd.

The interactive wizard itself isn't tested here — its job is I/O. The
file-writing and validation helpers are pure logic and testable.
"""

from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from google_ads_mcp.auth.credentials import Credentials
from google_ads_mcp.auth.init_cmd import validate_credentials, write_credentials


def _full_creds() -> Credentials:
    return Credentials(
        developer_token="dev-tok",
        oauth_client_id="cid",
        oauth_client_secret="csec",
        refresh_token="rtok",
        login_customer_id="1234567890",
    )


def _minimal_creds() -> Credentials:
    return Credentials(
        developer_token="dev-tok",
        oauth_client_id="cid",
        oauth_client_secret="csec",
        refresh_token="rtok",
    )


def test_write_credentials_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "credentials.yaml"
    creds = _full_creds()

    write_credentials(target, creds)

    parsed = yaml.safe_load(target.read_text())
    assert parsed["developer_token"] == "dev-tok"
    assert parsed["oauth_client_id"] == "cid"
    assert parsed["oauth_client_secret"] == "csec"
    assert parsed["refresh_token"] == "rtok"
    assert parsed["login_customer_id"] == "1234567890"


def test_write_credentials_omits_optional_mcc(tmp_path: Path) -> None:
    target = tmp_path / "credentials.yaml"

    write_credentials(target, _minimal_creds())

    parsed = yaml.safe_load(target.read_text())
    assert "login_customer_id" not in parsed


def test_write_credentials_creates_parent_dir(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "subdir" / "credentials.yaml"

    write_credentials(target, _minimal_creds())

    assert target.is_file()


def test_write_credentials_mode_0600(tmp_path: Path) -> None:
    target = tmp_path / "credentials.yaml"

    write_credentials(target, _minimal_creds())

    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_write_credentials_atomic_on_failure(tmp_path: Path) -> None:
    """If yaml.safe_dump raises, no partial file is left at the target path."""
    target = tmp_path / "credentials.yaml"
    creds = _minimal_creds()

    with (
        patch("google_ads_mcp.auth.init_cmd.yaml.safe_dump", side_effect=OSError("boom")),
        pytest.raises(OSError, match="boom"),
    ):
        write_credentials(target, creds)

    assert not target.exists()
    # No leftover temp files either.
    leftover = list(tmp_path.glob(".credentials.*"))
    assert leftover == []


def test_validate_credentials_returns_account_list() -> None:
    """validate_credentials wraps build_client + list_accessible cleanly."""
    fake_client = MagicMock()

    with (
        patch(
            "google_ads_mcp.auth.init_cmd.build_client", return_value=fake_client
        ) as build,
        patch(
            "google_ads_mcp.auth.init_cmd.accounts_impl.list_accessible",
            return_value=["1111111111", "2222222222"],
        ) as listf,
    ):
        result = validate_credentials(_full_creds())

    assert result == ["1111111111", "2222222222"]
    build.assert_called_once()
    listf.assert_called_once_with(fake_client)
