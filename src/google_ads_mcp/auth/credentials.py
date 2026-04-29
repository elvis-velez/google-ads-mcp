"""Credentials interface and data model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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
      sub-accounts. None for stand-alone (non-manager) installs.
    """

    developer_token: str
    oauth_client_id: str
    oauth_client_secret: str
    refresh_token: str
    login_customer_id: str | None = None


class CredentialsProvider(Protocol):
    """Source of Google Ads credentials.

    Implementations decide how to obtain and refresh credentials. Callers
    call `get()` once per server lifetime; rotating credentials means
    restarting the server.
    """

    def get(self) -> Credentials: ...
