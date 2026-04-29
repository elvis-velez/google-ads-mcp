# pyright: basic
"""Google Ads SDK client construction.

`build_client` is the only place we hand-build the SDK config dict. Everything
upstream takes a constructed client as a dependency, so swapping the auth
source later (OAuth proxy, service account) only changes this module.
"""

from __future__ import annotations

from typing import Any

from google.ads.googleads.client import GoogleAdsClient

from google_ads_mcp.auth.credentials import Credentials


def build_client(creds: Credentials) -> GoogleAdsClient:
    """Construct a GoogleAdsClient from internal `Credentials`.

    `use_proto_plus=True` means rows expose `row.campaign.name` etc. as plain
    Python attributes rather than the legacy proto2 accessor. Required for
    the dotted-path flattening in `gaql.search`.
    """
    config: dict[str, Any] = {
        "developer_token": creds.developer_token,
        "client_id": creds.oauth_client_id,
        "client_secret": creds.oauth_client_secret,
        "refresh_token": creds.refresh_token,
        "use_proto_plus": True,
    }
    if creds.login_customer_id is not None:
        config["login_customer_id"] = creds.login_customer_id
    return GoogleAdsClient.load_from_dict(config)
