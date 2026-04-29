# pyright: basic
"""Listing accessible customer accounts.

Powers the `gads-account://accessible` MCP resource. Strips the
"customers/" resource-name prefix Google returns, leaving plain 10-digit IDs.
"""

from __future__ import annotations

from google.ads.googleads.client import GoogleAdsClient

from google_ads_mcp.ads._errors import translate_errors


def list_accessible(client: GoogleAdsClient) -> list[str]:
    """Return the customer IDs the current credentials can operate on."""
    service = client.get_service("CustomerService")
    with translate_errors("ListAccessibleCustomers"):
        response = service.list_accessible_customers()
    # `resource_names` look like "customers/1234567890"; the LLM only needs the ID.
    return sorted(name.removeprefix("customers/") for name in response.resource_names)
