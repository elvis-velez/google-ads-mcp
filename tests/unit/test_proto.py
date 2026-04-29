"""Unit tests for the shared proto helpers.

Pure logic, used by both `ads.gaql` (response rows) and `ads.rpc` (response
messages). The flatten/coerce/size primitives are worth direct coverage.
"""

from __future__ import annotations

import enum
from types import SimpleNamespace

from google_ads_mcp.ads._proto import approximate_size, coerce, flatten


class _Status(enum.Enum):
    ENABLED = 2
    PAUSED = 3


def test_flatten_extracts_nested_attrs() -> None:
    row = SimpleNamespace(
        campaign=SimpleNamespace(id=42, name="Brand US"),
        metrics=SimpleNamespace(cost_micros=1_500_000),
    )

    flat = flatten(row, ["campaign.id", "campaign.name", "metrics.cost_micros"])

    assert flat == {
        "campaign.id": 42,
        "campaign.name": "Brand US",
        "metrics.cost_micros": 1_500_000,
    }


def test_flatten_missing_path_returns_none() -> None:
    row = SimpleNamespace(campaign=SimpleNamespace(id=1))

    flat = flatten(row, ["campaign.id", "campaign.does_not_exist"])

    assert flat["campaign.id"] == 1
    assert flat["campaign.does_not_exist"] is None


def test_coerce_enum_returns_name() -> None:
    assert coerce(_Status.ENABLED) == "ENABLED"


def test_coerce_passes_primitives() -> None:
    assert coerce(None) is None
    assert coerce(42) == 42
    assert coerce("hello") == "hello"
    assert coerce(True) is True


def test_approximate_size_grows_with_content() -> None:
    small = approximate_size({"a": 1})
    bigger = approximate_size({"campaign.name": "a really quite long campaign name"})

    assert small < bigger
    assert small > 0
