"""Interactive setup wizard.

Walks the operator through the only steps Google's process requires:
OAuth client (in Google Cloud Console), developer token (apicenter),
optional MCC manager-account ID, then the OAuth dance and a validation
round-trip.

Pure helpers (`write_credentials`, `validate_credentials`) live separately
so they can be unit-tested without prompting.
"""

from __future__ import annotations

import getpass
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from google_ads_mcp.ads import accounts as accounts_impl
from google_ads_mcp.ads.client import build_client
from google_ads_mcp.auth.credentials import Credentials
from google_ads_mcp.auth.oauth import run_loopback_flow
from google_ads_mcp.errors import AdsMcpError
from google_ads_mcp.settings import Settings

_BANNER = """\
google-ads-mcp setup
====================

This wizard will collect three pieces of Google-side configuration, then
run the OAuth consent flow to mint a refresh token. Steps:

  1. OAuth client ID + secret  (Google Cloud Console; one-time, ~5 min)
  2. Developer token           (Google Ads API Center; 1-3 business days approval)
  3. (Optional) Manager account ID, if your developer token is for an MCC

If you don't have an approved developer token yet, you can scaffold against
a test-account token; see
https://developers.google.com/google-ads/api/docs/best-practices/test-accounts
"""

_STEP_1 = """\
Step 1 / 3  --  OAuth client
----------------------------
You need an OAuth 2.0 "Desktop app" client. Create one at:

  https://console.cloud.google.com/apis/credentials

(If the project doesn't have the Google Ads API enabled, do that first under
"APIs & Services" -> "Enabled APIs & Services" -> "+ ENABLE APIS AND SERVICES".)

Create credentials -> OAuth client ID -> Application type: Desktop app.
Then note the client ID and client secret.
"""

_STEP_2 = """\
Step 2 / 3  --  Developer token
-------------------------------
Apply for a developer token from your Google Ads account's API Center:

  https://ads.google.com/aw/apicenter

Basic-access tokens (free) take 1-3 business days to approve. Once approved,
copy the developer token from the API Center page.
"""

_STEP_3 = """\
Step 3 / 3  --  Manager account (optional)
------------------------------------------
If your developer token is registered to a manager (MCC) account and you
want to operate on its sub-accounts, enter the MCC's customer ID here
(10 digits, no dashes). Leave blank if you're using a stand-alone account.
"""


def write_credentials(path: Path, creds: Credentials) -> None:
    """Atomically write credentials.yaml with mode 0600.

    Writes to a temp file in the same directory (so rename is atomic on
    POSIX) and chmods before rename so there's no readable-by-others window.
    """
    payload: dict[str, Any] = {
        "developer_token": creds.developer_token,
        "oauth_client_id": creds.oauth_client_id,
        "oauth_client_secret": creds.oauth_client_secret,
        "refresh_token": creds.refresh_token,
    }
    if creds.login_customer_id is not None:
        payload["login_customer_id"] = creds.login_customer_id

    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_str = tempfile.mkstemp(prefix=".credentials.", suffix=".yaml", dir=path.parent)
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(payload, f, default_flow_style=False)
        tmp.chmod(0o600)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def validate_credentials(creds: Credentials) -> list[str]:
    """Verify credentials work by listing accessible customer accounts.

    Returns the list of accessible customer IDs on success; raises on
    failure. Used by the init wizard to confirm setup before declaring
    success.
    """
    client = build_client(creds)
    return accounts_impl.list_accessible(client)


def _prompt(label: str, *, required: bool = True) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value or not required:
            return value
        print("  (required; please enter a value)")


def _prompt_secret(label: str) -> str:
    while True:
        value = getpass.getpass(f"{label}: ").strip()
        if value:
            return value
        print("  (required; please enter a value)")


def _prompt_optional(label: str) -> str | None:
    value = input(f"{label} [optional, press Enter to skip]: ").strip()
    return value or None


def run_init(settings: Settings | None = None) -> int:
    """Interactive setup. Returns a process exit code."""
    settings = settings or Settings()

    print(_BANNER)

    print(_STEP_1)
    oauth_client_id = _prompt("OAuth client ID")
    oauth_client_secret = _prompt_secret("OAuth client secret")

    print()
    print(_STEP_2)
    developer_token = _prompt_secret("Developer token")

    print()
    print(_STEP_3)
    login_customer_id = _prompt_optional("Manager customer ID")

    print()
    print("Opening browser for Google sign-in...")
    print("(If the browser doesn't open, copy the URL it prints into one manually.)")
    print()

    try:
        refresh_token = run_loopback_flow(
            oauth_client_id=oauth_client_id,
            oauth_client_secret=oauth_client_secret,
        )
    except AdsMcpError as e:
        print(f"\nOAuth flow failed: {e}")
        return 1

    creds = Credentials(
        developer_token=developer_token,
        oauth_client_id=oauth_client_id,
        oauth_client_secret=oauth_client_secret,
        refresh_token=refresh_token,
        login_customer_id=login_customer_id,
    )

    write_credentials(settings.credentials_path, creds)
    print(f"\nWrote credentials to {settings.credentials_path} (mode 0600).")

    print("Validating with ListAccessibleCustomers...")
    try:
        ids = validate_credentials(creds)
    except AdsMcpError as e:
        print(f"\nCredentials saved but validation failed: {e}")
        print("Re-run `google-ads-mcp init` after fixing the underlying issue.")
        return 1

    if ids:
        print(f"\nSetup complete. Accessible customer IDs: {', '.join(ids)}")
    else:
        print(
            "\nSetup complete, but no accessible customer accounts were returned. "
            "If you expected accounts, check that the Google account you signed in "
            "as has access to your Google Ads accounts."
        )
    return 0
