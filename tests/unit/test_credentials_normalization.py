"""Login-customer-id normalization on Credentials construction.

The Google Ads UI shows manager IDs as `123-456-7890`, but the SDK rejects
anything but bare 10-digit strings. Normalizing once at the data-model
boundary means every construction path (wizard, file load, test fixture)
behaves consistently.
"""

from __future__ import annotations

import pytest

from google_ads_mcp.auth.credentials import Credentials
from google_ads_mcp.errors import ConfigError


def _build(login_customer_id: str | None) -> Credentials:
    return Credentials(
        developer_token="dt",
        oauth_client_id="cid",
        oauth_client_secret="csec",
        refresh_token="rt",
        login_customer_id=login_customer_id,
    )


def test_strips_dashes() -> None:
    assert _build("123-456-7890").login_customer_id == "1234567890"


def test_strips_whitespace_and_dashes() -> None:
    assert _build(" 123-456-7890 ").login_customer_id == "1234567890"


def test_passes_through_clean_id() -> None:
    assert _build("1234567890").login_customer_id == "1234567890"


def test_none_stays_none() -> None:
    assert _build(None).login_customer_id is None


def test_empty_string_becomes_none() -> None:
    # The init wizard's "optional" prompt yields None on Enter, but defensive
    # fallback for hand-edited yaml with `login_customer_id: ''`.
    assert _build("").login_customer_id is None


def test_only_dashes_becomes_none() -> None:
    assert _build("---").login_customer_id is None


def test_wrong_digit_count_raises() -> None:
    with pytest.raises(ConfigError, match="10 digits"):
        _build("12345")


def test_too_many_digits_raises() -> None:
    with pytest.raises(ConfigError, match="10 digits"):
        _build("12345678901")
