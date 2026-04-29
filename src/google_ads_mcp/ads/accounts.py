# pyright: basic
"""Listing accessible customer accounts.

Powers the `gads-account://accessible` MCP resource. Strips the
"customers/" resource-name prefix Google returns, leaving plain 10-digit IDs.
"""

from __future__ import annotations

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

from google_ads_mcp.errors import ApiError


def list_accessible(client: GoogleAdsClient) -> list[str]:
    """Return the customer IDs the current credentials can operate on."""
    service = client.get_service("CustomerService")
    try:
        response = service.list_accessible_customers()
    except GoogleAdsException as e:
        raise ApiError(
            f"ListAccessibleCustomers failed: {e}",
            request_id=getattr(e, "request_id", None),
        ) from e
    # `resource_names` look like "customers/1234567890"; the LLM only needs the ID.
    return sorted(name.removeprefix("customers/") for name in response.resource_names)
