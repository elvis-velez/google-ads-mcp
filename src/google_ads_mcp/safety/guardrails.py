"""Threshold + structural guardrails.

Pure functions on `Operation`. Each raises `GuardrailViolation` with a
clear message naming the cap, the offending value, and the override
mechanism (where applicable). Call sites compose them in order; new
checks plug in by adding a function plus a single call.
"""

from __future__ import annotations

from google_ads_mcp.errors import GuardrailViolation
from google_ads_mcp.types import Operation

_USD_PER_MICRO = 1_000_000


def _micros_to_usd(micros: int) -> str:
    return f"${micros / _USD_PER_MICRO:,.2f}"


def check_batch_size(operations: list[Operation], *, max_size: int) -> None:
    """Reject mutate calls touching too many entities at once.

    Not overridable: the cap exists to keep diffs reviewable. Split into
    multiple mutate calls.
    """
    if len(operations) > max_size:
        raise GuardrailViolation(
            f"Batch too large: {len(operations)} operations exceed the cap of "
            f"{max_size}. Split into multiple mutate calls."
        )


def check_cpc(op: Operation, *, max_micros: int) -> None:
    """Reject ad-group-criterion (keyword) updates with bids above the cap.

    `force_override=True` on the operation bypasses (audited).
    """
    if op.force_override:
        return
    if op.service != "ad_group_criterion":
        return
    cpc = op.resource.get("cpc_bid_micros")
    if not isinstance(cpc, int):
        return
    if cpc > max_micros:
        raise GuardrailViolation(
            f"CPC bid {_micros_to_usd(cpc)} exceeds the cap of "
            f"{_micros_to_usd(max_micros)}. Pass force_override=true on this "
            f"operation to bypass."
        )


def check_budget(op: Operation, *, max_micros: int) -> None:
    """Reject campaign-budget operations with daily amount above the cap.

    `force_override=True` on the operation bypasses (audited).
    """
    if op.force_override:
        return
    if op.service != "campaign_budget":
        return
    amount = op.resource.get("amount_micros")
    if not isinstance(amount, int):
        return
    if amount > max_micros:
        raise GuardrailViolation(
            f"Daily budget {_micros_to_usd(amount)} exceeds the cap of "
            f"{_micros_to_usd(max_micros)}. Pass force_override=true on this "
            f"operation to bypass."
        )
