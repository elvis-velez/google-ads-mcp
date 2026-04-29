# pyright: basic
"""Field discovery via GoogleAdsFieldService.

Powers the `gads-schema://{resource_type}` MCP resource. Field metadata
changes rarely; the calling resource caches results for the server lifetime.
"""

from __future__ import annotations

from google.ads.googleads.client import GoogleAdsClient

from google_ads_mcp.ads._errors import translate_errors
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
    with translate_errors(f"GoogleAdsFieldService[{resource_type}]"):
        response = service.search_google_ads_fields(query=query)

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
