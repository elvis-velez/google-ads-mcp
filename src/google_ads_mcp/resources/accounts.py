"""`gads-account://accessible` resource.

Lists the customer IDs the current credentials can operate on. Cached for
the server lifetime — the list rarely changes during a session.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from google_ads_mcp.ads import accounts as accounts_impl
from google_ads_mcp.types import AccessibleAccounts


def register_accounts(mcp: FastMCP, *, client: Any) -> None:
    """Register the accessible-accounts resource."""

    cached: AccessibleAccounts | None = None
    lock = asyncio.Lock()

    @mcp.resource(
        "gads-account://accessible",
        name="accessible-accounts",
        description=(
            "Google Ads customer IDs the current credentials can operate on. "
            "10-digit numeric strings, no dashes."
        ),
    )
    async def accessible() -> AccessibleAccounts:  # pyright: ignore[reportUnusedFunction]
        nonlocal cached
        async with lock:
            if cached is None:
                ids = await asyncio.to_thread(accounts_impl.list_accessible, client)
                cached = AccessibleAccounts(customer_ids=ids)
            return cached
