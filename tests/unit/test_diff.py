"""Tests for the operation-diff renderer."""

from __future__ import annotations

from google_ads_mcp.safety import diff
from google_ads_mcp.types import Operation, RpcCall


def test_remove_renders_resource_name() -> None:
    op = Operation(
        service="campaign",
        op="remove",
        resource={"resource_name": "customers/1/campaigns/2"},
    )

    d = diff.render(op)

    assert d.op == "remove"
    assert "customers/1/campaigns/2" in d.summary
    assert "customers/1/campaigns/2" in d.detail
    assert d.detail.startswith("Will remove")


def test_create_renders_payload_fields() -> None:
    op = Operation(
        service="campaign_budget",
        op="create",
        resource={
            "name": "Q2 budget",
            "amount_micros": 50_000_000,
            "delivery_method": "STANDARD",
        },
    )

    d = diff.render(op)

    assert d.op == "create"
    assert "Will create campaign_budget" in d.detail
    # Fields appear in sorted order:
    detail_lines = d.detail.splitlines()
    assert any("amount_micros: 50000000" in ln for ln in detail_lines)
    assert any("name: Q2 budget" in ln for ln in detail_lines)


def test_update_lists_masked_fields() -> None:
    op = Operation(
        service="campaign",
        op="update",
        resource={
            "resource_name": "customers/1/campaigns/2",
            "status": "PAUSED",
            "name": "Renamed",  # NOT in update_mask, won't be applied
        },
        update_mask=["status"],
    )

    d = diff.render(op)

    assert d.op == "update"
    assert "customers/1/campaigns/2" in d.summary
    assert "status: PAUSED" in d.detail
    # The unmasked field is NOT prominently shown in the masked-fields list:
    masked_section = d.detail[d.detail.index("masked fields:"):]
    assert "name" not in masked_section


def test_update_without_mask_warns() -> None:
    op = Operation(
        service="campaign",
        op="update",
        resource={"resource_name": "customers/1/campaigns/2", "status": "PAUSED"},
        update_mask=None,
    )

    d = diff.render(op)

    assert "no update_mask" in d.detail


def test_update_renders_nested_dotted_mask_path() -> None:
    """Dotted update_mask paths (e.g. 'target_spend.target_spend_micros')
    should walk into nested dicts, not look up the dotted string as a flat key."""
    op = Operation(
        service="campaign",
        op="update",
        resource={
            "resource_name": "customers/1/campaigns/2",
            "target_spend": {"target_spend_micros": 5_000_000},
        },
        update_mask=["target_spend.target_spend_micros"],
    )

    d = diff.render(op)

    assert "target_spend.target_spend_micros: 5000000" in d.detail
    assert "<not in payload>" not in d.detail


def test_update_renders_missing_path_with_marker() -> None:
    """When the update_mask names a field absent from the resource dict,
    the renderer flags it explicitly so the LLM can spot the inconsistency."""
    op = Operation(
        service="campaign",
        op="update",
        resource={"resource_name": "customers/1/campaigns/2"},
        update_mask=["status"],
    )

    d = diff.render(op)

    assert "status: <not in payload>" in d.detail


# === RPC-call diffs ========================================================


def test_render_rpc_call_summary_and_detail() -> None:
    rpc = RpcCall(
        service="recommendation_service",
        method="apply_recommendation",
        params={"resource_name": "customers/1/recommendations/abc"},
    )

    d = diff.render_rpc_call(rpc)

    assert d.kind == "rpc_call"
    assert d.service == "recommendation_service"
    assert d.method == "apply_recommendation"
    assert d.summary == "rpc recommendation_service.apply_recommendation"
    assert d.detail.startswith(
        "Will call recommendation_service.apply_recommendation with:"
    )
    assert "resource_name: customers/1/recommendations/abc" in d.detail


def test_render_rpc_call_with_no_params() -> None:
    rpc = RpcCall(
        service="payments_account_service",
        method="list_payments_accounts",
        params={},
    )

    d = diff.render_rpc_call(rpc)

    assert "(no params)" in d.detail


def test_render_rpc_call_sorts_params() -> None:
    """Detail lines list params in sorted order — stable diffs across runs."""
    rpc = RpcCall(
        service="experiment_service",
        method="promote_experiment",
        params={"zeta": 1, "alpha": 2, "mu": 3},
    )

    d = diff.render_rpc_call(rpc)

    detail_lines = d.detail.splitlines()[1:]  # skip the leading "Will call ..." line
    assert detail_lines == ["  alpha: 2", "  mu: 3", "  zeta: 1"]
