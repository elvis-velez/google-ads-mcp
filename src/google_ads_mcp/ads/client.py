# pyright: basic
"""Google Ads SDK client construction.

`build_client` is the only place we hand-build the SDK config dict. Everything
upstream takes a constructed client as a dependency, so swapping the auth
source later (OAuth proxy, service account) only changes this module.
"""

from __future__ import annotations

import logging
from typing import Any

from google.ads.googleads.client import GoogleAdsClient

from google_ads_mcp.auth.credentials import Credentials


def _silence_sdk_request_log() -> None:
    """Quiet the SDK's per-request stderr logger.

    The SDK's `LoggingInterceptor` emits a `Request made: ...` line on every
    API call: at INFO for successes and at WARNING for failures. Both are
    redundant — successes don't need narrating and failures already propagate
    as `ApiError` with a structured message we surface to the LLM. Set to
    ERROR so the noise is gone; if a future SDK warning matters we'll find
    it via the exception path, not the log.

    Must be called AFTER `load_from_dict`; that helper configures logging
    when a `logging` key is present in config and would overwrite earlier
    setLevel calls. Setting both the parent and the interceptor's specific
    child logger is belt-and-suspenders against SDK reorganisation.
    """
    logging.getLogger("google.ads.googleads").setLevel(logging.ERROR)
    logging.getLogger("google.ads.googleads.client").setLevel(logging.ERROR)


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
    client = GoogleAdsClient.load_from_dict(config)
    _silence_sdk_request_log()
    return client
