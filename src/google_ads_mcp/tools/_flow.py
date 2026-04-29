"""Shared mutate-flow orchestrator.

Both the Layer-2 `mutate` tool and every Layer-1 outcome tool walk the same
safety steps: customer-allowlist check → API validate-only → diff render →
store under a mutate_id. Centralised here so the contract is one code path;
tool-level files are pure Operation constructors that delegate.

Every outcome (ok, guardrail_rejection, validation_failed, api_error)
produces exactly one audit-log entry — failures included. That's the
forensic guarantee: if an op was attempted, audit.log shows what happened.

This helper lives in `tools/` rather than `safety/` because it imports
`ads/` (for the validate-only API call). `safety/` stays SDK-free as a
deliberate boundary.
"""

from __future__ import annotations

from typing import Any

from google_ads_mcp.ads import mutate as mutate_impl
from google_ads_mcp.ads import rpc as rpc_impl
from google_ads_mcp.errors import ApiError, GuardrailViolation, ValidationFailed
from google_ads_mcp.observability.audit import AuditEvent, AuditLogger
from google_ads_mcp.safety import diff
from google_ads_mcp.safety.allowlist import CustomerAllowlist, check_customer_allowlist
from google_ads_mcp.safety.pending import PendingStore
from google_ads_mcp.types import (
    CustomerId,
    MutatePreview,
    Operation,
    OperationsPayload,
    PreviewDiff,
    RpcCall,
    RpcCallPayload,
)


def perform_mutate(
    *,
    client: Any,
    customer_id: CustomerId,
    operations: list[Operation],
    allowlist: CustomerAllowlist,
    pending: PendingStore,
    audit: AuditLogger,
) -> MutatePreview:
    """Run the full Layer-2 mutate flow synchronously.

    Callers wrap this in `asyncio.to_thread`; the function itself is sync.
    """
    # --- guardrails ---------------------------------------------------------
    try:
        check_customer_allowlist(customer_id, allowlist=allowlist)
    except GuardrailViolation as e:
        audit.record(_event_error("preview", "guardrail_rejection", e, customer_id, operations))
        raise

    # --- validate via SDK ---------------------------------------------------
    try:
        mutate_impl.mutate(client, customer_id, operations, validate_only=True)
    except ValidationFailed as e:
        audit.record(_event_error("preview", "validation_failed", e, customer_id, operations))
        raise
    except ApiError as e:
        audit.record(_event_error("preview", "api_error", e, customer_id, operations))
        raise

    # --- diff + store -------------------------------------------------------
    diffs: list[PreviewDiff] = [diff.render(op) for op in operations]
    mutate_id, expires_at = pending.store(
        customer_id=customer_id,
        payload=OperationsPayload(operations=operations),
    )

    audit.record(
        AuditEvent(
            phase="preview",
            outcome="ok",
            mutate_id=mutate_id,
            customer_id=customer_id,
            payload_kind="operations",
            operations=operations,
            rpc_call=None,
            resource_names=None,
            error_type=None,
            error_message=None,
            error_request_id=None,
        )
    )

    return MutatePreview(
        mutate_id=mutate_id,
        customer_id=customer_id,
        operations_count=len(operations),
        diffs=diffs,
        expires_at_iso=expires_at.isoformat(),
    )


def _event_error(
    phase: str,
    outcome: str,
    error: Exception,
    customer_id: str | None,
    operations: list[Operation] | None,
) -> AuditEvent:
    """Compact builder for failure-path audit events (operations path)."""
    return AuditEvent(
        phase=phase,  # type: ignore[arg-type]  # call-site uses the literal values
        outcome=outcome,  # type: ignore[arg-type]
        mutate_id=None,
        customer_id=customer_id,
        payload_kind="operations",
        operations=operations,
        rpc_call=None,
        resource_names=None,
        error_type=type(error).__name__,
        error_message=str(error),
        error_request_id=getattr(error, "request_id", None),
    )


def perform_mutate_rpc(
    *,
    client: Any,
    customer_id: CustomerId,
    rpc_call: RpcCall,
    supports_validate_only: bool,
    allowlist: CustomerAllowlist,
    pending: PendingStore,
    audit: AuditLogger,
) -> MutatePreview:
    """Run the preview phase for a mutating RPC.

    Mirrors `perform_mutate` for `GoogleAdsService.Mutate`-shaped writes:

    1. allowlist check (customer_id must be reachable by these credentials)
    2. if the request type has `validate_only`, round-trip with it set to
       True so the API gets a chance to reject the call without applying
    3. render an `RpcCallDiff` (client-side; same pattern as Operation diffs)
    4. store under a mutate_id for `apply()` to consume
    5. audit "preview ok"

    `supports_validate_only` is the catalog signal for whether step 2 is
    available — passed in rather than re-introspected so the dispatch path
    is testable without a live SDK.
    """
    try:
        check_customer_allowlist(customer_id, allowlist=allowlist)
    except GuardrailViolation as e:
        audit.record(_event_error_rpc("preview", "guardrail_rejection", e, customer_id, rpc_call))
        raise

    if supports_validate_only:
        try:
            rpc_impl.invoke(
                client,
                rpc_call.service,
                rpc_call.method,
                rpc_call.params,
                customer_id=customer_id,
                validate_only=True,
            )
        except ValidationFailed as e:
            audit.record(_event_error_rpc("preview", "validation_failed", e, customer_id, rpc_call))
            raise
        except ApiError as e:
            audit.record(_event_error_rpc("preview", "api_error", e, customer_id, rpc_call))
            raise

    diffs: list[PreviewDiff] = [diff.render_rpc_call(rpc_call)]
    mutate_id, expires_at = pending.store(
        customer_id=customer_id,
        payload=RpcCallPayload(rpc_call=rpc_call),
    )

    audit.record(
        AuditEvent(
            phase="preview",
            outcome="ok",
            mutate_id=mutate_id,
            customer_id=customer_id,
            payload_kind="rpc_call",
            operations=None,
            rpc_call=rpc_call,
            resource_names=None,
            error_type=None,
            error_message=None,
            error_request_id=None,
        )
    )

    return MutatePreview(
        mutate_id=mutate_id,
        customer_id=customer_id,
        operations_count=1,
        diffs=diffs,
        expires_at_iso=expires_at.isoformat(),
    )


def _event_error_rpc(
    phase: str,
    outcome: str,
    error: Exception,
    customer_id: str | None,
    rpc_call: RpcCall | None,
) -> AuditEvent:
    """Compact builder for failure-path audit events (RPC path)."""
    return AuditEvent(
        phase=phase,  # type: ignore[arg-type]
        outcome=outcome,  # type: ignore[arg-type]
        mutate_id=None,
        customer_id=customer_id,
        payload_kind="rpc_call",
        operations=None,
        rpc_call=rpc_call,
        resource_names=None,
        error_type=type(error).__name__,
        error_message=str(error),
        error_request_id=getattr(error, "request_id", None),
    )
