"""`gads-schema://{resource_type}` resource.

Returns selectable / filterable / sortable fields for a Google Ads resource
type, sourced from `GoogleAdsFieldService`. Per-type cache lives for the
server lifetime; field metadata almost never changes.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from google_ads_mcp.ads import schema as schema_impl
from google_ads_mcp.types import ResourceFields


def register_schema(mcp: FastMCP, *, client: Any) -> None:
    """Register the schema-lookup resource."""

    cache: dict[str, ResourceFields] = {}
    lock = asyncio.Lock()

    @mcp.resource(
        "gads-schema://{resource_type}",
        name="resource-schema",
        description=(
            "Selectable / filterable / sortable fields for a Google Ads "
            "resource type (e.g. campaign, ad_group, keyword_view)."
        ),
    )
    async def schema(resource_type: str) -> ResourceFields:  # pyright: ignore[reportUnusedFunction]
        async with lock:
            if resource_type not in cache:
                cache[resource_type] = await asyncio.to_thread(
                    schema_impl.get_resource_fields, client, resource_type
                )
            return cache[resource_type]
