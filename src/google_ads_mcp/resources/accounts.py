"""`gads-account://accessible` resource.

Lists the customer IDs the current credentials can operate on, served from
the same `CustomerAllowlist` instance the Layer-2 guardrails check against.
Single source of truth — what the resource shows is exactly what the
allowlist enforces.
"""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from google_ads_mcp.safety.allowlist import CustomerAllowlist
from google_ads_mcp.types import AccessibleAccounts


def register_accounts(mcp: FastMCP, *, allowlist: CustomerAllowlist) -> None:
    """Register the accessible-accounts resource."""

    @mcp.resource(
        "gads-account://accessible",
        name="accessible-accounts",
        description=(
            "Google Ads customer IDs the current credentials can operate on. "
            "10-digit numeric strings, no dashes."
        ),
    )
    async def accessible() -> AccessibleAccounts:  # pyright: ignore[reportUnusedFunction]
        ids = await asyncio.to_thread(allowlist.all)
        return AccessibleAccounts(customer_ids=ids)
