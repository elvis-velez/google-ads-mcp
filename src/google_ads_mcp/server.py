"""FastMCP server entry point.

`build_server()` is the dependency-injection seam: production wires real
settings, credentials, and an SDK client; tests pass fakes (or a pre-built
mock client) to keep unit tests credential-free.

`run()` is the production hook used by the `serve` CLI subcommand.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from google_ads_mcp.ads import accounts as accounts_impl
from google_ads_mcp.ads.client import build_client
from google_ads_mcp.auth.credentials import CredentialsProvider
from google_ads_mcp.auth.local import LocalRefreshTokenCredentials
from google_ads_mcp.observability.activity import (
    ActivityLogger,
    ActivityRecorder,
    JsonlActivityLogger,
)
from google_ads_mcp.observability.audit import AuditLogger, JsonlAuditLogger
from google_ads_mcp.observability.clock import Clock, SystemClock
from google_ads_mcp.resources.accounts import register_accounts
from google_ads_mcp.resources.rpc_catalog import register_rpc_catalog
from google_ads_mcp.resources.rpc_schema import register_rpc_schema
from google_ads_mcp.resources.schema import register_schema
from google_ads_mcp.safety.allowlist import CustomerAllowlist
from google_ads_mcp.safety.pending import PendingStore
from google_ads_mcp.settings import Settings
from google_ads_mcp.tools.layer1 import register_layer1
from google_ads_mcp.tools.layer2 import register_layer2

_log = logging.getLogger(__name__)


def build_server(
    *,
    settings: Settings | None = None,
    credentials_provider: CredentialsProvider | None = None,
    client: Any | None = None,
    clock: Clock | None = None,
    audit: AuditLogger | None = None,
    activity: ActivityLogger | None = None,
    pending: PendingStore | None = None,
    allowlist: CustomerAllowlist | None = None,
) -> FastMCP:
    """Construct and configure the MCP server.

    Inject any dependency for tests. When `client` is given, credential
    loading is skipped — that's how unit tests build a server without
    touching credentials.yaml. Production wires all defaults here so the
    cooperating components share a single Settings + Clock + AuditLogger.
    """
    settings = settings or Settings()
    clock = clock or SystemClock()

    if client is None:
        if credentials_provider is None:
            credentials_provider = LocalRefreshTokenCredentials(settings.credentials_path)
        client = build_client(credentials_provider.get())

    audit = audit or JsonlAuditLogger(path=settings.audit_log_path, clock=clock)
    activity = activity or JsonlActivityLogger(
        path=settings.activity_log_path, clock=clock
    )
    activity_recorder = ActivityRecorder(logger=activity, clock=clock)
    pending = pending or PendingStore(
        clock=clock,
        ttl=timedelta(seconds=settings.mutate_id_ttl_seconds),
    )
    bound_client = client  # capture for the closure (lambda below)
    allowlist = allowlist or CustomerAllowlist(
        fetch=lambda: accounts_impl.list_accessible(bound_client),
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

    register_layer2(
        mcp,
        client=client,
        settings=settings,
        pending=pending,
        audit=audit,
        activity=activity_recorder,
        allowlist=allowlist,
    )
    register_layer1(
        mcp,
        client=client,
        pending=pending,
        allowlist=allowlist,
        audit=audit,
        activity=activity_recorder,
    )
    register_accounts(mcp, allowlist=allowlist, activity=activity_recorder)
    register_schema(mcp, client=client, activity=activity_recorder)
    register_rpc_catalog(mcp, client=client, activity=activity_recorder)
    register_rpc_schema(mcp, client=client, activity=activity_recorder)

    _log.info(
        "server constructed (audit=%s activity=%s)",
        settings.audit_log_path,
        settings.activity_log_path,
    )

    return mcp


def run() -> None:
    """Run the server over stdio. Entry point for the `serve` subcommand."""
    _log.info("starting google-ads-mcp on stdio transport")
    server = build_server()
    try:
        server.run(transport="stdio")
    finally:
        _log.info("google-ads-mcp stopped")
