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

from google_ads_mcp.ads import _proto
from google_ads_mcp.ads import gaql as gaql_impl
from google_ads_mcp.ads import mutate as mutate_impl
from google_ads_mcp.ads import rpc as rpc_impl
from google_ads_mcp.errors import PendingExpired, PendingNotFound, ValidationFailed
from google_ads_mcp.observability.activity import ActivityRecorder, with_activity
from google_ads_mcp.observability.audit import AuditEvent, AuditLogger
from google_ads_mcp.safety.allowlist import CustomerAllowlist, check_customer_allowlist
from google_ads_mcp.safety.pending import PendingStore
from google_ads_mcp.settings import Settings
from google_ads_mcp.tools._flow import perform_mutate, perform_mutate_rpc
from google_ads_mcp.types import (
    ApplyResult,
    GaqlResult,
    MutatePreview,
    Operation,
    PendingPayload,
    RpcCall,
)


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
                pattern=r"^\d{10}$",
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
            title="Call a read-only Google Ads RPC",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="call_read_rpc")
    async def call_read_rpc(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[
            str,
            Field(description="10-digit Google Ads customer ID, no dashes."),
        ],
        service: Annotated[
            str,
            Field(
                description=(
                    "Snake_case service name, e.g. 'keyword_plan_idea_service' "
                    "or 'recommendation_service'. Discover via the "
                    "gads-rpc-catalog:// resource (filter for read_only=true)."
                ),
            ),
        ],
        method: Annotated[
            str,
            Field(
                description=(
                    "Snake_case RPC method, e.g. 'generate_keyword_ideas'. "
                    "Must be a read-only method (get_*, list_*, search_*, "
                    "generate_*, suggest_*, fetch_*); writes go through "
                    "call_mutate_rpc. See gads-rpc-schema://{service}/{method} "
                    "for request fields."
                ),
            ),
        ],
        params: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Request fields by name. customer_id is auto-injected and "
                    "need not be passed. Unknown fields are rejected — consult "
                    "the schema resource before calling."
                ),
            ),
        ],
    ) -> dict[str, Any]:
        """Generic read-only RPC against any v24 Google Ads service.

        Covers the long tail of read RPCs that don't fit GAQL — keyword
        ideas, reach forecasts, audience insights, benchmarks, suggestions,
        list_invoices, list_payments_accounts, etc. Refuses methods that
        don't look like reads to keep writes routed through call_mutate_rpc.
        """
        if not rpc_impl.looks_read_only(method):
            raise ValidationFailed(
                f"Method '{method}' does not look like a read (must start with "
                "get_, list_, search, generate_, suggest_, or fetch_). Use "
                "call_mutate_rpc for write RPCs."
            )

        def go() -> dict[str, Any]:
            check_customer_allowlist(customer_id, allowlist=allowlist)
            response = rpc_impl.invoke(
                client,
                service,
                method,
                params,
                customer_id=customer_id,
                validate_only=None,
            )
            return _proto.message_to_dict(response)

        return await asyncio.to_thread(go)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Preview a mutate (validate-only)",
            # `validate_only=true` means Google Ads itself isn't mutated, but
            # the call still mints a new pending mutate_id, appends to the
            # audit log, and consumes API quota. Treat as not-read-only so
            # clients don't speculatively re-issue. Destructive intent only
            # lands on `apply`.
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="mutate")
    async def mutate(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[
            str,
            Field(
                pattern=r"^\d{10}$",
                description="10-digit Google Ads customer ID, no dashes.",
            ),
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
            title="Preview a mutating RPC (validate-only when supported)",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="call_mutate_rpc")
    async def call_mutate_rpc(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[
            str,
            Field(
                pattern=r"^\d{10}$",
                description="10-digit Google Ads customer ID, no dashes.",
            ),
        ],
        service: Annotated[
            str,
            Field(
                description=(
                    "Snake_case service name, e.g. 'experiment_service' or "
                    "'recommendation_service'. Discover via gads-rpc-catalog:// "
                    "(filter for read_only=false)."
                ),
            ),
        ],
        method: Annotated[
            str,
            Field(
                description=(
                    "Snake_case mutating RPC method, e.g. 'promote_experiment'. "
                    "Read-only methods are rejected — use call_read_rpc for those. "
                    "See gads-rpc-schema://{service}/{method} for request fields."
                ),
            ),
        ],
        params: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Request fields by name. customer_id is auto-injected. When "
                    "the request type supports validate_only, the preview "
                    "round-trips to the API for validation. Otherwise the preview "
                    "is client-side only — the LLM should treat the diff as "
                    "best-effort."
                ),
            ),
        ],
    ) -> MutatePreview:
        """Preview a write-side RPC against any v24 Google Ads service.

        Covers the long tail of mutating RPCs that don't fit the unified
        `MutateOperation` shape — recommendation apply/dismiss, experiment
        lifecycle, campaign-draft promotion, MCC management, link services,
        conversion uploads, etc. Returns a mutate_id; call apply() to commit.
        """
        if rpc_impl.looks_read_only(method):
            raise ValidationFailed(
                f"Method '{method}' looks read-only. Use call_read_rpc for "
                "reads (no preview/apply). call_mutate_rpc is for writes."
            )

        # Detect validate_only support up-front so the flow can decide whether
        # to round-trip or render a client-side-only preview.
        supports_validate_only = await asyncio.to_thread(
            _supports_validate_only, client, method
        )
        rpc_call = RpcCall(service=service, method=method, params=params)

        return await asyncio.to_thread(
            perform_mutate_rpc,
            client=client,
            customer_id=customer_id,
            rpc_call=rpc_call,
            supports_validate_only=supports_validate_only,
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

        def applier(customer_id: str, payload: PendingPayload) -> ApplyResult:
            if payload.kind == "operations":
                return _apply_operations(
                    customer_id, payload.operations, mutate_id=mutate_id,
                    client=client, audit=audit,
                )
            return _apply_rpc_call(
                customer_id, payload.rpc_call, mutate_id=mutate_id,
                client=client, audit=audit,
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
                    payload_kind=None,  # unknown — entry was missing
                    operations=None,
                    rpc_call=None,
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
                    payload_kind=None,  # original entry was audited at first commit
                    operations=None,
                    rpc_call=None,
                    resource_names=result.resource_names,
                    error_type=None,
                    error_message=None,
                    error_request_id=None,
                )
            )

        return result


def _apply_operations(
    customer_id: str,
    ops: list[Operation],
    *,
    mutate_id: str,
    client: Any,
    audit: AuditLogger,
) -> ApplyResult:
    """Commit a list of MutateOperation-shaped writes via GoogleAdsService.Mutate."""
    try:
        resource_names = mutate_impl.mutate(client, customer_id, ops, validate_only=False)
    except Exception as e:
        audit.record(
            AuditEvent(
                phase="apply",
                outcome="api_error",
                mutate_id=mutate_id,
                customer_id=customer_id,
                payload_kind="operations",
                operations=ops,
                rpc_call=None,
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
            payload_kind="operations",
            operations=ops,
            rpc_call=None,
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


def _apply_rpc_call(
    customer_id: str,
    rpc_call: RpcCall,
    *,
    mutate_id: str,
    client: Any,
    audit: AuditLogger,
) -> ApplyResult:
    """Commit a single mutating RPC via the generic dispatcher."""
    try:
        response = rpc_impl.invoke(
            client,
            rpc_call.service,
            rpc_call.method,
            rpc_call.params,
            customer_id=customer_id,
            validate_only=False,
        )
    except Exception as e:
        audit.record(
            AuditEvent(
                phase="apply",
                outcome="api_error",
                mutate_id=mutate_id,
                customer_id=customer_id,
                payload_kind="rpc_call",
                operations=None,
                rpc_call=rpc_call,
                resource_names=None,
                error_type=type(e).__name__,
                error_message=str(e),
                error_request_id=getattr(e, "request_id", None),
            )
        )
        raise

    resource_names = _extract_resource_names(response)
    audit.record(
        AuditEvent(
            phase="apply",
            outcome="ok",
            mutate_id=mutate_id,
            customer_id=customer_id,
            payload_kind="rpc_call",
            operations=None,
            rpc_call=rpc_call,
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


def _extract_resource_names(response: Any) -> list[str]:
    """Best-effort scan of a write-RPC response for resource_name fields.

    Most write RPCs return either a top-level `resource_name` (e.g. account
    link creation) or a `results` repeated field whose entries each have a
    `resource_name` (e.g. ApplyRecommendationResponse). We marshal once and
    walk the dict; absent any matches, return [] — the audit log still has
    the request payload so forensics aren't lost.
    """
    response_dict: dict[str, Any] = _proto.message_to_dict(response)
    out: list[str] = []
    top = response_dict.get("resource_name")
    if isinstance(top, str):
        out.append(top)
    results = response_dict.get("results")
    if isinstance(results, list):
        for entry in results:  # type: ignore[reportUnknownVariableType]
            if isinstance(entry, dict):
                rn = entry.get("resource_name")  # type: ignore[reportUnknownMemberType]
                if isinstance(rn, str):
                    out.append(rn)
    return out


def _supports_validate_only(client: Any, method: str) -> bool:
    """Module-level wrapper so the registration closure can pass a function
    reference into asyncio.to_thread without capturing client in a lambda."""
    return rpc_impl.request_supports_validate_only(client, method)
