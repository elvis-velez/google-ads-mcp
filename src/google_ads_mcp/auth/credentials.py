"""Credentials interface and data model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from google_ads_mcp.errors import ConfigError


@dataclass(frozen=True, slots=True)
class Credentials:
    """Everything needed to authenticate against the Google Ads API.

    - `developer_token`: identifies the calling app to Google. Obtained via
      apicenter; approval takes 1-3 business days.
    - `oauth_client_id` / `oauth_client_secret`: from a Google Cloud OAuth
      client of type "Desktop app". The loopback flow used by `init` requires
      the desktop variant.
    - `refresh_token`: minted once during the OAuth consent step and stored
      locally. Long-lived but revocable from the Google account's connected-
      apps page.
    - `login_customer_id`: the manager (MCC) account ID, required when the
      caller's developer token belongs to a manager and operations target
      sub-accounts. None for stand-alone (non-manager) installs. Dashes and
      whitespace are stripped on construction since the Google Ads UI shows
      IDs in `123-456-7890` form but the SDK rejects anything but bare digits.
    """

    developer_token: str
    oauth_client_id: str
    oauth_client_secret: str
    refresh_token: str
    login_customer_id: str | None = None

    def __post_init__(self) -> None:
        if self.login_customer_id is None:
            return
        digits = "".join(ch for ch in self.login_customer_id if ch.isdigit())
        if not digits:
            # Treat blank/non-digit input as "no MCC".
            object.__setattr__(self, "login_customer_id", None)
            return
        if len(digits) != 10:
            raise ConfigError(
                f"login_customer_id must be 10 digits (with or without dashes); "
                f"got {self.login_customer_id!r} which contains {len(digits)} digit(s)."
            )
        object.__setattr__(self, "login_customer_id", digits)


class CredentialsProvider(Protocol):
    """Source of Google Ads credentials.

    Implementations decide how to obtain and refresh credentials. Callers
    call `get()` once per server lifetime; rotating credentials means
    restarting the server.
    """

    def get(self) -> Credentials: ...
