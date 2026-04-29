"""Shared mutate-flow orchestrator.

Both the Layer-2 `mutate` tool and every Layer-1 outcome tool walk the same
safety steps: customer-allowlist check → batch-size cap → CPC/budget caps
→ API validate-only → diff render → store under a mutate_id. Centralised
here so the contract is one code path; tool-level files are pure Operation
constructors that delegate.

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
from google_ads_mcp.errors import ApiError, GuardrailViolation, ValidationFailed
from google_ads_mcp.observability.audit import AuditEvent, AuditLogger
from google_ads_mcp.safety import diff, guardrails
from google_ads_mcp.safety.allowlist import CustomerAllowlist
from google_ads_mcp.safety.limits import LimitsConfig
from google_ads_mcp.safety.pending import PendingStore
from google_ads_mcp.settings import Settings
from google_ads_mcp.types import CustomerId, MutatePreview, Operation


def perform_mutate(
    *,
    client: Any,
    customer_id: CustomerId,
    operations: list[Operation],
    settings: Settings,
    allowlist: CustomerAllowlist,
    limits: LimitsConfig,
    pending: PendingStore,
    audit: AuditLogger,
) -> MutatePreview:
    """Run the full Layer-2 mutate flow synchronously.

    Callers wrap this in `asyncio.to_thread`; the function itself is sync.
    """
    # --- guardrails ---------------------------------------------------------
    try:
        guardrails.check_customer_allowlist(customer_id, allowlist=allowlist)
        guardrails.check_batch_size(operations, max_size=settings.mutate_max_ops_per_call)
        account_limits = limits.for_customer(customer_id)
        for op in operations:
            guardrails.check_cpc(op, max_micros=account_limits.cpc_max_micros)
            guardrails.check_budget(op, max_micros=account_limits.budget_max_daily_micros)
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
    diffs = [diff.render(op) for op in operations]
    mutate_id, expires_at = pending.store(
        customer_id=customer_id, operations=operations
    )

    audit.record(
        AuditEvent(
            phase="preview",
            outcome="ok",
            mutate_id=mutate_id,
            customer_id=customer_id,
            operations=operations,
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
    """Compact builder for failure-path audit events."""
    return AuditEvent(
        phase=phase,  # type: ignore[arg-type]  # call-site uses the literal values
        outcome=outcome,  # type: ignore[arg-type]
        mutate_id=None,
        customer_id=customer_id,
        operations=operations,
        resource_names=None,
        error_type=type(error).__name__,
        error_message=str(error),
        error_request_id=getattr(error, "request_id", None),
    )
