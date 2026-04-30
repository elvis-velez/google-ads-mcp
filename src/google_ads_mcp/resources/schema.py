"""`gads-schema://{resource_type}` resource.

Returns selectable / filterable / sortable fields for a Google Ads resource
type, sourced from `GoogleAdsFieldService`. Per-type cache lives for the
server lifetime; field metadata almost never changes.

Also registers a completion handler so MCP clients can suggest valid
`resource_type` values to the LLM as it constructs URIs against the
template — invalid types silently 404 from the Ads API otherwise.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
)

from google_ads_mcp.ads import schema as schema_impl
from google_ads_mcp.observability.activity import ActivityRecorder, with_activity
from google_ads_mcp.types import ResourceFields

# Curated subset of Google Ads resources the LLM is overwhelmingly likely
# to want. The full set has ~150 entries; suggesting all of them would just
# be noise. Add to this list when a real workflow exposes a gap.
_COMPLETIONS: tuple[str, ...] = (
    "ad_group",
    "ad_group_ad",
    "ad_group_criterion",
    "ad_group_simulation",
    "asset",
    "asset_group",
    "bidding_strategy",
    "campaign",
    "campaign_budget",
    "campaign_criterion",
    "campaign_simulation",
    "change_event",
    "conversion_action",
    "customer",
    "geographic_view",
    "keyword_view",
    "search_term_view",
    "shopping_performance_view",
    "shopping_product",
)


def register_schema(
    mcp: FastMCP,
    *,
    client: Any,
    activity: ActivityRecorder,
) -> None:
    """Register the schema-lookup resource and its completion handler."""

    cache: dict[str, ResourceFields] = {}
    lock = asyncio.Lock()

    @mcp.resource(
        "gads-schema://{resource_type}",
        name="resource-schema",
        title="Google Ads Resource Schema",
        description=(
            "Selectable / filterable / sortable fields for a Google Ads "
            "resource type (e.g. campaign, ad_group, keyword_view)."
        ),
        mime_type="application/json",
    )
    @with_activity(activity, name="gads-schema://{resource_type}", kind="resource")
    async def schema(resource_type: str) -> ResourceFields:  # pyright: ignore[reportUnusedFunction]
        async with lock:
            if resource_type not in cache:
                cache[resource_type] = await asyncio.to_thread(
                    schema_impl.get_resource_fields, client, resource_type
                )
            return cache[resource_type]

    @mcp.completion()
    async def complete(  # pyright: ignore[reportUnusedFunction]
        ref: ResourceTemplateReference | PromptReference,
        argument: CompletionArgument,
        _context: CompletionContext | None,
    ) -> Completion | None:
        if not isinstance(ref, ResourceTemplateReference):
            return None
        if ref.uri != "gads-schema://{resource_type}":
            return None
        if argument.name != "resource_type":
            return None
        prefix = argument.value or ""
        matches = [name for name in _COMPLETIONS if name.startswith(prefix)]
        return Completion(values=matches, total=len(matches), hasMore=False)
