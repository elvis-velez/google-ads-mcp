"""Tests for the operation-diff renderer."""

from __future__ import annotations

from google_ads_mcp.safety import diff
from google_ads_mcp.types import Operation


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


