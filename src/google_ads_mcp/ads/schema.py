# pyright: basic
"""Field discovery via GoogleAdsFieldService.

Powers the `gads-schema://{resource_type}` MCP resource. Field metadata
changes rarely; the calling resource caches results for the server lifetime.
"""

from __future__ import annotations

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

from google_ads_mcp.errors import ApiError
from google_ads_mcp.types import ResourceFields


def get_resource_fields(client: GoogleAdsClient, resource_type: str) -> ResourceFields:
    """Look up which fields exist on a resource and how each can be used."""
    service = client.get_service("GoogleAdsFieldService")
    # GoogleAdsFieldService uses a GAQL-like syntax restricted to field metadata.
    # Filtering by name prefix returns just the requested resource's fields.
    query = (
        "SELECT name, selectable, filterable, sortable "
        f"WHERE name LIKE '{resource_type}.%'"
    )
    try:
        response = service.search_google_ads_fields(query=query)
    except GoogleAdsException as e:
        raise ApiError(
            f"GoogleAdsFieldService query for resource '{resource_type}' failed: {e}",
            request_id=getattr(e, "request_id", None),
        ) from e

    selectable: list[str] = []
    filterable: list[str] = []
    sortable: list[str] = []
    for field in response:
        if field.selectable:
            selectable.append(field.name)
        if field.filterable:
            filterable.append(field.name)
        if field.sortable:
            sortable.append(field.name)

    return ResourceFields(
        resource_type=resource_type,
        selectable=sorted(selectable),
        filterable=sorted(filterable),
        sortable=sorted(sortable),
    )
