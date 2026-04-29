"""Tests for the threshold + structural guardrails."""

from __future__ import annotations

import pytest

from google_ads_mcp.errors import GuardrailViolation
from google_ads_mcp.safety import guardrails
from google_ads_mcp.types import Operation


def _campaign_pause_op() -> Operation:
    return Operation(
        service="campaign",
        op="update",
        resource={"resource_name": "customers/1/campaigns/2", "status": "PAUSED"},
        update_mask=["status"],
    )


def _budget_op(amount_micros: int, *, force_override: bool = False) -> Operation:
    return Operation(
        service="campaign_budget",
        op="update",
        resource={
            "resource_name": "customers/1/campaignBudgets/3",
            "amount_micros": amount_micros,
        },
        update_mask=["amount_micros"],
        force_override=force_override,
    )


def _cpc_op(cpc_bid_micros: int, *, force_override: bool = False) -> Operation:
    return Operation(
        service="ad_group_criterion",
        op="update",
        resource={
            "resource_name": "customers/1/adGroupCriteria/3~4",
            "cpc_bid_micros": cpc_bid_micros,
        },
        update_mask=["cpc_bid_micros"],
        force_override=force_override,
    )


# --- batch size --------------------------------------------------------------


def test_batch_size_allows_at_cap() -> None:
    ops = [_campaign_pause_op() for _ in range(100)]
    guardrails.check_batch_size(ops, max_size=100)  # no raise


def test_batch_size_rejects_over_cap() -> None:
    ops = [_campaign_pause_op() for _ in range(101)]
    with pytest.raises(GuardrailViolation, match="Batch too large"):
        guardrails.check_batch_size(ops, max_size=100)


# --- CPC ---------------------------------------------------------------------


def test_cpc_under_cap_allowed() -> None:
    guardrails.check_cpc(_cpc_op(10_000_000), max_micros=50_000_000)  # $10 < $50


def test_cpc_over_cap_rejected() -> None:
    with pytest.raises(GuardrailViolation, match=r"CPC bid \$60\.00"):
        guardrails.check_cpc(_cpc_op(60_000_000), max_micros=50_000_000)


def test_cpc_force_override_bypasses() -> None:
    op = _cpc_op(60_000_000, force_override=True)
    guardrails.check_cpc(op, max_micros=50_000_000)  # no raise


def test_cpc_check_ignores_other_services() -> None:
    # A campaign update with a stray cpc_bid_micros field shouldn't trip CPC check
    # — it's only meaningful on ad_group_criterion.
    guardrails.check_cpc(_campaign_pause_op(), max_micros=50_000_000)


def test_cpc_check_ignores_missing_field() -> None:
    op = Operation(
        service="ad_group_criterion",
        op="update",
        resource={"resource_name": "x", "negative": True},
        update_mask=["negative"],
    )
    guardrails.check_cpc(op, max_micros=50_000_000)  # no cpc_bid_micros set


# --- budget ------------------------------------------------------------------


def test_budget_under_cap_allowed() -> None:
    guardrails.check_budget(_budget_op(500_000_000), max_micros=1_000_000_000)


def test_budget_over_cap_rejected() -> None:
    with pytest.raises(GuardrailViolation, match=r"Daily budget \$1,500\.00"):
        guardrails.check_budget(_budget_op(1_500_000_000), max_micros=1_000_000_000)


def test_budget_force_override_bypasses() -> None:
    op = _budget_op(1_500_000_000, force_override=True)
    guardrails.check_budget(op, max_micros=1_000_000_000)
