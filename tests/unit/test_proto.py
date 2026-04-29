"""Unit tests for the shared proto helpers.

Pure logic, used by both `ads.gaql` (response rows) and `ads.rpc` (response
messages). The flatten/coerce/size primitives are worth direct coverage.

`message_to_dict` is also tested here against the three real response shapes
the SDK returns: plain proto-plus Message, Pager (paginated wrapper), and
long-running operation. A bug in any of these is the difference between a
clean response and "Unknown field for X: DESCRIPTOR".
"""

from __future__ import annotations

import enum
from types import SimpleNamespace

import proto

from google_ads_mcp.ads._proto import approximate_size, coerce, flatten, message_to_dict


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


# === message_to_dict ========================================================


class _Sample(proto.Message):  # type: ignore[misc]  # proto base class is dynamic
    # proto-plus uses metaclass magic so `: str = proto.Field(...)` resolves
    # to a string field at runtime; pyright can't follow the descriptor and
    # treats the assignment literally — silence per-line.
    name: str = proto.Field(proto.STRING, number=1)  # type: ignore[assignment]
    score: int = proto.Field(proto.INT32, number=2)  # type: ignore[assignment]


def test_message_to_dict_proto_plus_message() -> None:
    """Plain proto-plus Message — uses Message.to_dict, no DESCRIPTOR access."""
    msg = _Sample(name="x", score=42)

    out = message_to_dict(msg)

    assert out == {"name": "x", "score": 42}


def test_message_to_dict_unwraps_pager() -> None:
    """Pager wrapper — proto-plus client libs expose `_response` for paginated RPCs."""
    inner = _Sample(name="page-y", score=7)
    pager = SimpleNamespace(_response=inner)

    out = message_to_dict(pager)

    assert out == {"name": "page-y", "score": 7}


def test_message_to_dict_long_running_operation() -> None:
    """Long-running ops (google.api_core.operation.Operation) expose .operation
    as a raw google.longrunning.Operation. We snapshot it via MessageToDict."""
    from google.longrunning.operations_pb2 import Operation as LROOperation

    inner_op = LROOperation(name="operations/some-batch-job-id", done=False)
    wrapper = SimpleNamespace(operation=inner_op)

    out = message_to_dict(wrapper)

    assert out["name"] == "operations/some-batch-job-id"
    # `done` field defaults to False; MessageToDict omits default scalars,
    # so absence-of-key is the expected shape here.
    assert out.get("done") in (False, None)
