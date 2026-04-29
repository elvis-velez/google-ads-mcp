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
from google_ads_mcp.safety import diff, guardrails
from google_ads_mcp.safety.audit import AuditLogger
from google_ads_mcp.safety.pending import PendingStore
from google_ads_mcp.settings import Settings
from google_ads_mcp.types import ApplyResult, GaqlResult, MutatePreview, Operation


def register_layer2(
    mcp: FastMCP,
    *,
    client: Any,
    settings: Settings,
    pending: PendingStore,
    audit: AuditLogger,
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

        Runs server-side guardrails (CPC, budget, batch-size), calls the API
        with `validate_only=true`, renders a per-operation diff, and stores
        the operations under a `mutate_id`. Call `apply(mutate_id)` to commit.
        """
        # Guardrails are pure-sync; run inline before crossing into the SDK.
        guardrails.check_batch_size(operations, max_size=settings.mutate_max_ops_per_call)
        for op in operations:
            guardrails.check_cpc(op, max_micros=settings.cpc_max_micros)
            guardrails.check_budget(op, max_micros=settings.budget_max_daily_micros)

        # Validate via SDK; a failure raises ApiError (translated by the boundary).
        await asyncio.to_thread(
            mutate_impl.mutate,
            client,
            customer_id,
            operations,
            validate_only=True,
        )

        diffs = [diff.render(op) for op in operations]
        mutate_id, expires_at = pending.store(
            customer_id=customer_id, operations=operations
        )

        return MutatePreview(
            mutate_id=mutate_id,
            customer_id=customer_id,
            operations_count=len(operations),
            diffs=diffs,
            expires_at_iso=expires_at.isoformat(),
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
            resource_names = mutate_impl.mutate(
                client, customer_id, ops, validate_only=False
            )
            audit.log_apply(
                mutate_id=mutate_id,
                customer_id=customer_id,
                operations=ops,
                resource_names=resource_names,
            )
            return ApplyResult(
                mutate_id=mutate_id,
                customer_id=customer_id,
                applied=True,
                resource_names=resource_names,
            )

        return await asyncio.to_thread(pending.apply, mutate_id, applier)
