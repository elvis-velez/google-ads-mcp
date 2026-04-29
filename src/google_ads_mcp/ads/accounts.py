# pyright: basic
"""Listing accessible customer accounts.

Powers the `gads-account://accessible` MCP resource and the customer-id
allowlist. Returns plain 10-digit IDs (the API gives "customers/<id>"
resource names; the leading prefix is stripped here).

Two functions:

- `list_accessible` — wraps `CustomerService.ListAccessibleCustomers`,
  which returns only direct-membership accounts (the user is explicitly a
  member). Sub-accounts reached via a manager are NOT included.
- `list_subaccounts` — given a manager customer_id, queries
  `customer_client` to enumerate everything in its tree below the
  manager. Used to flesh out the allowlist for users whose typical
  workflow is "I'm a manager, operate on my managed sub-accounts."

The allowlist construction in `server.py` calls both and unions the
results, so the user gets one set covering both topologies.
"""

from __future__ import annotations

from google.ads.googleads.client import GoogleAdsClient

from google_ads_mcp.ads._errors import translate_errors


def list_accessible(client: GoogleAdsClient) -> list[str]:
    """Return the customer IDs the current credentials are direct members of."""
    service = client.get_service("CustomerService")
    with translate_errors("ListAccessibleCustomers"):
        response = service.list_accessible_customers()
    return sorted(name.removeprefix("customers/") for name in response.resource_names)


def list_subaccounts(client: GoogleAdsClient, manager_id: str) -> list[str]:
    """Enumerate all sub-accounts under `manager_id` via `customer_client`.

    Returns the leaf customer IDs (level > 0); the manager itself is excluded
    since it's already in the direct-accessible list. If `manager_id` isn't
    actually a manager, the query returns no level>0 rows and we return [].
    """
    service = client.get_service("GoogleAdsService")
    query = (
        "SELECT customer_client.id "
        "FROM customer_client "
        "WHERE customer_client.level > 0"
    )
    with translate_errors(f"customer_client[manager={manager_id}]"):
        stream = service.search_stream(customer_id=manager_id, query=query)
        ids: list[str] = []
        for batch in stream:
            for row in batch.results:
                ids.append(str(row.customer_client.id))
    return sorted(set(ids))
