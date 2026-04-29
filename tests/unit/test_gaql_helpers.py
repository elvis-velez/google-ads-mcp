# pyright: reportPrivateUsage=false
"""Unit tests for the pure helpers inside ads.gaql.

The `search` function itself is exercised by integration tests against
Google's test-account environment; mocking the SDK at unit level would be
high-maintenance for low signal. The flatten/coerce/size helpers are pure
logic and worth direct coverage.
"""

from __future__ import annotations

import enum
from types import SimpleNamespace

from google_ads_mcp.ads.gaql import _approximate_size, _coerce, _flatten


class _Status(enum.Enum):
    ENABLED = 2
    PAUSED = 3


def test_flatten_extracts_nested_attrs() -> None:
    row = SimpleNamespace(
        campaign=SimpleNamespace(id=42, name="Brand US"),
        metrics=SimpleNamespace(cost_micros=1_500_000),
    )

    flat = _flatten(row, ["campaign.id", "campaign.name", "metrics.cost_micros"])

    assert flat == {
        "campaign.id": 42,
        "campaign.name": "Brand US",
        "metrics.cost_micros": 1_500_000,
    }


def test_flatten_missing_path_returns_none() -> None:
    row = SimpleNamespace(campaign=SimpleNamespace(id=1))

    flat = _flatten(row, ["campaign.id", "campaign.does_not_exist"])

    assert flat["campaign.id"] == 1
    assert flat["campaign.does_not_exist"] is None


def test_coerce_enum_returns_name() -> None:
    assert _coerce(_Status.ENABLED) == "ENABLED"


def test_coerce_passes_primitives() -> None:
    assert _coerce(None) is None
    assert _coerce(42) == 42
    assert _coerce("hello") == "hello"
    assert _coerce(True) is True


def test_approximate_size_grows_with_content() -> None:
    small = _approximate_size({"a": 1})
    bigger = _approximate_size({"campaign.name": "a really quite long campaign name"})

    assert small < bigger
    # Sanity: not zero for non-empty input.
    assert small > 0
