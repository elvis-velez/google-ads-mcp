"""Layer 2 tools — the generic escape hatches.

`gaql` covers all reads; `mutate`/`apply` (Phase 2) cover all writes. Layer 1
outcome tools route through these so safety, audit, and diff apply uniformly.

The vendor `GoogleAdsClient` is passed as `Any` here on purpose: knowledge of
the SDK's types lives in `ads/`, not `tools/`. Forwarding the client through
this layer doesn't require us to import it.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from google_ads_mcp.ads import gaql as gaql_impl
from google_ads_mcp.settings import Settings
from google_ads_mcp.types import GaqlResult


def register_layer2(mcp: FastMCP, *, client: Any, settings: Settings) -> None:
    """Register Layer 2 tools onto the given FastMCP server."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Run a GAQL query",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    async def gaql(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[
            str,
            Field(
                description=(
                    "10-digit Google Ads customer ID, no dashes (e.g. '1234567890'). "
                    "Use the gads-account://accessible resource to discover IDs."
                ),
            ),
        ],
        query: Annotated[
            str,
            Field(
                description=(
                    "GAQL SELECT statement. See the gads-schema://{resource_type} "
                    "resource to discover field names. Results are capped by row "
                    "count and byte budget; check `truncated` and use LIMIT/OFFSET "
                    "to page if needed."
                ),
            ),
        ],
    ) -> GaqlResult:
        """Run a Google Ads Query Language SELECT and return matching rows.

        Returns rows as flat dicts keyed by the dotted field paths from the
        SELECT clause. Sets `truncated=true` when row or byte caps are hit.
        """
        return await asyncio.to_thread(
            gaql_impl.search,
            client,
            customer_id,
            query,
            max_rows=settings.gaql_max_rows,
            max_bytes=settings.gaql_max_response_bytes,
        )
