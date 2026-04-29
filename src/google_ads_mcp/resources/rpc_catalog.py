"""`gads-rpc-catalog://` resource.

Flat list of every public RPC on every v24 service the SDK ships, with
hints the LLM needs before calling `call_read_rpc` / `call_mutate_rpc`:

- `read_only`: heuristic — get_/list_/search/generate_/suggest_/fetch_
- `supports_validate_only`: whether the request type has a validate_only
  field (relevant for call_mutate_rpc preview/apply)
- `request_type`: StudlyCase request proto name (used by the schema
  resource to look up field details)

Built once at server start by introspecting the SDK and cached for the
server's lifetime — the catalog is static for any given SDK version.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from google_ads_mcp.ads import rpc as rpc_impl
from google_ads_mcp.observability.activity import ActivityRecorder, with_activity


def register_rpc_catalog(
    mcp: FastMCP,
    *,
    client: Any,
    activity: ActivityRecorder,
) -> None:
    """Register `gads-rpc-catalog://`."""

    cache: list[dict[str, Any]] | None = None
    lock = asyncio.Lock()

    @mcp.resource(
        "gads-rpc-catalog://",
        name="rpc-catalog",
        description=(
            "Catalog of every public Google Ads RPC available through "
            "call_read_rpc / call_mutate_rpc. Each entry has service, method, "
            "read_only, supports_validate_only, request_type. Use the "
            "gads-rpc-schema://{service}/{method} resource for per-method "
            "request fields."
        ),
        mime_type="application/json",
    )
    @with_activity(activity, name="gads-rpc-catalog://", kind="resource")
    async def catalog() -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        nonlocal cache
        async with lock:
            if cache is None:
                descriptors = await asyncio.to_thread(rpc_impl.catalog, client)
                cache = [asdict(d) for d in descriptors]
            return {"rpcs": cache}
