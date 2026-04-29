"""Layer 2 tools — the generic escape hatches.

`gaql` covers all reads; `mutate`/`apply` cover all writes. Layer 1 outcome
tools route through these so safety, audit, and diff apply uniformly.

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
from google_ads_mcp.ads import mutate as mutate_impl
from google_ads_mcp.errors import PendingExpired, PendingNotFound
from google_ads_mcp.observability.activity import ActivityRecorder, with_activity
from google_ads_mcp.observability.audit import AuditEvent, AuditLogger
from google_ads_mcp.safety.allowlist import CustomerAllowlist, check_customer_allowlist
from google_ads_mcp.safety.pending import PendingStore
from google_ads_mcp.settings import Settings
from google_ads_mcp.tools._flow import perform_mutate
from google_ads_mcp.types import ApplyResult, GaqlResult, MutatePreview, Operation


def register_layer2(
    mcp: FastMCP,
    *,
    client: Any,
    settings: Settings,
    pending: PendingStore,
    audit: AuditLogger,
    activity: ActivityRecorder,
    allowlist: CustomerAllowlist,
) -> None:
    """Register Layer 2 tools onto the given FastMCP server."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Run a GAQL query",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="gaql")
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

        def go() -> GaqlResult:
            check_customer_allowlist(customer_id, allowlist=allowlist)
            return gaql_impl.search(
                client,
                customer_id,
                query,
                max_rows=settings.gaql_max_rows,
                max_bytes=settings.gaql_max_response_bytes,
            )

        return await asyncio.to_thread(go)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Preview a mutate (validate-only)",
            # mutate is read-only with respect to Google Ads — validate_only=true
            # means the API is consulted but no changes are made. The caller must
            # explicitly invoke `apply` to commit.
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="mutate")
    async def mutate(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[
            str,
            Field(description="10-digit Google Ads customer ID, no dashes."),
        ],
        operations: Annotated[
            list[Operation],
            Field(
                description=(
                    "List of mutate operations. Each has service (snake_case, e.g. "
                    "'campaign'), op (create/update/remove), resource (fields), and "
                    "update_mask (required for update). v1 supports campaign only at "
                    "Layer 2; use Layer-1 outcome tools for other services."
                ),
            ),
        ],
    ) -> MutatePreview:
        """Validate operations against the API and return a previewable mutate_id.

        Verifies the customer-id allowlist, calls the API with
        `validate_only=true`, renders a per-operation diff, and stores the
        operations under a `mutate_id`. Call `apply(mutate_id)` to commit.
        """

        return await asyncio.to_thread(
            perform_mutate,
            client=client,
            customer_id=customer_id,
            operations=operations,
            allowlist=allowlist,
            pending=pending,
            audit=audit,
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Apply a previewed mutate",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="apply")
    async def apply(  # pyright: ignore[reportUnusedFunction]
        mutate_id: Annotated[
            str,
            Field(description="The mutate_id returned by a previous `mutate()` call."),
        ],
    ) -> ApplyResult:
        """Commit a previously previewed mutate.

        Idempotent: re-applying the same mutate_id returns the cached result
        with `applied=false` and does not re-call the API. mutate_ids have a
        TTL; expired ids raise PendingExpired and require re-issuing the
        mutate.
        """

        def applier(customer_id: str, ops: list[Operation]) -> ApplyResult:
            try:
                resource_names = mutate_impl.mutate(
                    client, customer_id, ops, validate_only=False
                )
            except Exception as e:
                audit.record(
                    AuditEvent(
                        phase="apply",
                        outcome="api_error",
                        mutate_id=mutate_id,
                        customer_id=customer_id,
                        operations=ops,
                        resource_names=None,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        error_request_id=getattr(e, "request_id", None),
                    )
                )
                raise

            audit.record(
                AuditEvent(
                    phase="apply",
                    outcome="ok",
                    mutate_id=mutate_id,
                    customer_id=customer_id,
                    operations=ops,
                    resource_names=resource_names,
                    error_type=None,
                    error_message=None,
                    error_request_id=None,
                )
            )
            return ApplyResult(
                mutate_id=mutate_id,
                customer_id=customer_id,
                applied=True,
                resource_names=resource_names,
            )

        try:
            result = await asyncio.to_thread(pending.apply, mutate_id, applier)
        except (PendingNotFound, PendingExpired) as e:
            outcome = "not_found" if isinstance(e, PendingNotFound) else "expired"
            audit.record(
                AuditEvent(
                    phase="apply",
                    outcome=outcome,
                    mutate_id=mutate_id,
                    customer_id=None,  # unknown — entry was missing
                    operations=None,
                    resource_names=None,
                    error_type=type(e).__name__,
                    error_message=str(e),
                    error_request_id=None,
                )
            )
            raise

        # `applied=False` means pending.apply returned the cached result
        # without re-running the applier — record the replay separately so
        # auditors can distinguish a fresh commit from an idempotent re-call.
        if not result.applied:
            audit.record(
                AuditEvent(
                    phase="apply",
                    outcome="cached_replay",
                    mutate_id=mutate_id,
                    customer_id=result.customer_id,
                    operations=None,  # already audited at first commit
                    resource_names=result.resource_names,
                    error_type=None,
                    error_message=None,
                    error_request_id=None,
                )
            )

        return result
