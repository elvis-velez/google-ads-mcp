"""FastMCP server entry point.

`build_server()` is the dependency-injection seam: production wires real
settings, credentials, and an SDK client; tests pass fakes (or a pre-built
mock client) to keep unit tests credential-free.

`run()` is the production hook used by the `serve` CLI subcommand.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from google_ads_mcp.ads.client import build_client
from google_ads_mcp.auth.credentials import CredentialsProvider
from google_ads_mcp.auth.local import LocalRefreshTokenCredentials
from google_ads_mcp.resources.accounts import register_accounts
from google_ads_mcp.resources.schema import register_schema
from google_ads_mcp.safety.audit import AuditLogger, JsonlAuditLogger
from google_ads_mcp.safety.clock import Clock, SystemClock
from google_ads_mcp.safety.pending import PendingStore
from google_ads_mcp.settings import Settings
from google_ads_mcp.tools.layer2 import register_layer2


def build_server(
    *,
    settings: Settings | None = None,
    credentials_provider: CredentialsProvider | None = None,
    client: Any | None = None,
    clock: Clock | None = None,
    audit: AuditLogger | None = None,
    pending: PendingStore | None = None,
) -> FastMCP:
    """Construct and configure the MCP server.

    Inject `settings`, `credentials_provider`, `client`, `clock`, `audit`, or
    `pending` for tests. When `client` is given, credential loading is skipped
    entirely — that's how unit tests build a server without touching
    credentials.yaml. The other defaults are wired here so production gets a
    consistent set of cooperating components.
    """
    settings = settings or Settings()
    clock = clock or SystemClock()

    if client is None:
        if credentials_provider is None:
            credentials_provider = LocalRefreshTokenCredentials(settings.credentials_path)
        client = build_client(credentials_provider.get())

    audit = audit or JsonlAuditLogger(path=settings.audit_log_path, clock=clock)
    pending = pending or PendingStore(
        clock=clock,
        ttl=timedelta(seconds=settings.mutate_id_ttl_seconds),
    )

    mcp = FastMCP("google-ads-mcp")

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Connectivity ping",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=False,
        ),
    )
    def ping() -> str:  # pyright: ignore[reportUnusedFunction]
        """Returns 'pong'. Smoke test that the server is reachable."""
        return "pong"

    register_layer2(mcp, client=client, settings=settings, pending=pending, audit=audit)
    register_accounts(mcp, client=client)
    register_schema(mcp, client=client)

    return mcp


def run() -> None:
    """Run the server over stdio. Entry point for the `serve` subcommand."""
    server = build_server()
    server.run(transport="stdio")
