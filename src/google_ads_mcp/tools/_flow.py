"""Shared mutate-flow orchestrator.

Both the Layer-2 `mutate` tool and every Layer-1 outcome tool (pause_campaign,
set_campaign_budget, ...) walk the same safety steps: customer-allowlist
check → batch-size cap → CPC/budget caps → API validate-only → diff render
→ store under a mutate_id. Centralised here so the contract is one code
path; tool-level files are pure Operation constructors that delegate.

This helper deliberately lives in `tools/` rather than `safety/` because
it imports `ads/` (for the validate-only API call) — `safety/` stays
SDK-free as a deliberate boundary.
"""

from __future__ import annotations

from typing import Any

from google_ads_mcp.ads import mutate as mutate_impl
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
) -> MutatePreview:
    """Run the full Layer-2 mutate flow synchronously.

    Callers (the Layer-2 mutate tool, every Layer-1 outcome tool) wrap this
    in `asyncio.to_thread`; this function itself is sync.
    """
    guardrails.check_customer_allowlist(customer_id, allowlist=allowlist)
    guardrails.check_batch_size(operations, max_size=settings.mutate_max_ops_per_call)

    account_limits = limits.for_customer(customer_id)
    for op in operations:
        guardrails.check_cpc(op, max_micros=account_limits.cpc_max_micros)
        guardrails.check_budget(op, max_micros=account_limits.budget_max_daily_micros)

    mutate_impl.mutate(client, customer_id, operations, validate_only=True)

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
