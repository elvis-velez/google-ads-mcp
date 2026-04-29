# pyright: basic
"""OAuth 2.0 installed-app loopback flow.

Google deprecated the OOB (`urn:ietf:wg:oauth:2.0:oob`) flow. Loopback is now
the only supported flow for desktop apps: the local helper opens a browser
to Google's consent screen, runs an HTTP server on `localhost`, captures the
authorization code from the redirect, and exchanges it for tokens.

Requires the Google Cloud OAuth client to be of type "Desktop app".
"""

from __future__ import annotations

from google_auth_oauthlib.flow import InstalledAppFlow

from google_ads_mcp.errors import AuthError

# `adwords` is the OAuth scope name for the Google Ads API. Google did not
# rename it when "AdWords" became "Google Ads" in 2018; the scope still works.
ADS_SCOPE = "https://www.googleapis.com/auth/adwords"


def run_loopback_flow(*, oauth_client_id: str, oauth_client_secret: str) -> str:
    """Run the installed-app loopback flow and return a refresh token.

    Picks a free localhost port automatically. The caller is responsible
    for any UX around opening the browser; google-auth-oauthlib does that
    by default and prints a fallback URL if the browser launch fails.
    """
    client_config = {
        "installed": {
            "client_id": oauth_client_id,
            "client_secret": oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            # Google accepts http://localhost without a registered specific port
            # for desktop OAuth clients, so we let the helper pick port 0 (free).
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=[ADS_SCOPE])
    # `prompt="consent"` forces Google to issue a fresh refresh_token even
    # when the user has previously granted access; without it, returning users
    # get an access_token only and we'd have nothing to persist.
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        open_browser=True,
    )

    refresh_token = getattr(creds, "refresh_token", None)
    if not refresh_token:
        raise AuthError(
            "Google did not return a refresh token. Re-run init and ensure "
            "you grant consent on the Google sign-in page."
        )
    return str(refresh_token)
