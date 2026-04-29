"""`gads-rpc-schema://{service}/{method}` resource.

Returns the request proto schema for one RPC: field names, types, labels
(REPEATED / OPTIONAL), nested message types, enum value lists, and oneof
groups. The LLM consults this before constructing a `params` dict for
`call_read_rpc` / `call_mutate_rpc`.

Per-method cache is populated lazily and lives for the server lifetime —
proto schemas don't change inside a process.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from google_ads_mcp.ads import rpc as rpc_impl
from google_ads_mcp.observability.activity import ActivityRecorder, with_activity


def register_rpc_schema(
    mcp: FastMCP,
    *,
    client: Any,
    activity: ActivityRecorder,
) -> None:
    """Register `gads-rpc-schema://{service}/{method}`."""

    cache: dict[tuple[str, str], dict[str, Any]] = {}
    lock = asyncio.Lock()

    @mcp.resource(
        "gads-rpc-schema://{service}/{method}",
        name="rpc-schema",
        description=(
            "Request proto schema for one Google Ads RPC. Returns fields "
            "(name, type, label, message_type, enum_values) and oneof groups. "
            "Use after gads-rpc-catalog:// has surfaced a candidate method."
        ),
        mime_type="application/json",
    )
    @with_activity(activity, name="gads-rpc-schema://{service}/{method}", kind="resource")
    async def schema(service: str, method: str) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        key = (service, method)
        async with lock:
            if key not in cache:
                cache[key] = await asyncio.to_thread(
                    rpc_impl.request_schema, client, service, method
                )
            return cache[key]
