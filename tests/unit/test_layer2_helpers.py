# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownLambdaType=false, reportUnknownVariableType=false
"""Tests for the module-level helpers in tools.layer2.

These are the helpers the apply() applier closure delegates to —
_apply_operations, _apply_rpc_call, and _extract_resource_names. Each is
exercised through the tool wrapper indirectly, but direct coverage makes
regressions on response-shape edge cases visible.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from google_ads_mcp.observability.audit import AuditEvent
from google_ads_mcp.tools import layer2
from google_ads_mcp.types import Operation, RpcCall


def _msg(d: dict[str, Any]) -> Any:
    """Build a fake response message that `_proto.message_to_dict` can flatten.

    `message_to_dict` calls `type(msg).pb(msg)` and feeds the result to
    `MessageToDict`. We bypass that path by handing it a real proto-plus
    message — except we don't want to drag in real protos. Instead we
    monkey-patch `_proto.message_to_dict` for these tests.
    """
    return SimpleNamespace(_dict=d)


def test_extract_resource_names_top_level(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        layer2._proto,  # type: ignore[attr-defined]
        "message_to_dict",
        lambda m: m._dict,
    )

    response = _msg({"resource_name": "customers/1/accountLinks/abc"})

    assert layer2._extract_resource_names(response) == [
        "customers/1/accountLinks/abc"
    ]


def test_extract_resource_names_results_array(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        layer2._proto,  # type: ignore[attr-defined]
        "message_to_dict",
        lambda m: m._dict,
    )

    # ApplyRecommendationResponse-shaped: a `results` repeated field.
    response = _msg({
        "results": [
            {"resource_name": "customers/1/recommendations/r1"},
            {"resource_name": "customers/1/recommendations/r2"},
        ],
    })

    assert layer2._extract_resource_names(response) == [
        "customers/1/recommendations/r1",
        "customers/1/recommendations/r2",
    ]


def test_extract_resource_names_combines_top_and_results(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        layer2._proto,  # type: ignore[attr-defined]
        "message_to_dict",
        lambda m: m._dict,
    )

    response = _msg({
        "resource_name": "customers/1/parent",
        "results": [{"resource_name": "customers/1/child"}],
    })

    assert layer2._extract_resource_names(response) == [
        "customers/1/parent",
        "customers/1/child",
    ]


def test_extract_resource_names_returns_empty_when_neither(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        layer2._proto,  # type: ignore[attr-defined]
        "message_to_dict",
        lambda m: m._dict,
    )

    response = _msg({"some_other_field": "value", "results": "not-a-list"})

    assert layer2._extract_resource_names(response) == []


def test_extract_resource_names_skips_non_dict_results_entries(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """A `results` entry that's not a dict (shouldn't happen but defensive)
    is silently skipped — never raises."""
    monkeypatch.setattr(
        layer2._proto,  # type: ignore[attr-defined]
        "message_to_dict",
        lambda m: m._dict,
    )

    response = _msg({
        "results": [
            {"resource_name": "customers/1/x"},
            "stray-string",
            {"resource_name": "customers/1/y"},
        ],
    })

    assert layer2._extract_resource_names(response) == [
        "customers/1/x",
        "customers/1/y",
    ]


# === _apply_operations / _apply_rpc_call ===================================


class _RecordingAudit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)


def test_apply_operations_audits_ok(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The Operations path delegates to ads.mutate.mutate(validate_only=False)
    and writes a single 'apply ok' audit event with payload_kind=operations."""
    monkeypatch.setattr(
        layer2.mutate_impl,  # type: ignore[attr-defined]
        "mutate",
        lambda _client, _customer_id, _ops, *, validate_only: ["customers/1/campaigns/2"],
    )

    audit = _RecordingAudit()
    op = Operation(
        service="campaign",
        op="update",
        resource={"resource_name": "customers/1/campaigns/2", "status": "PAUSED"},
        update_mask=["status"],
    )

    result = layer2._apply_operations(
        "1234567890", [op],
        mutate_id="abc", client=object(), audit=audit,
    )

    assert result.applied is True
    assert result.resource_names == ["customers/1/campaigns/2"]
    assert len(audit.events) == 1
    e = audit.events[0]
    assert e.phase == "apply"
    assert e.outcome == "ok"
    assert e.payload_kind == "operations"
    assert e.operations == [op]
    assert e.rpc_call is None


def test_apply_operations_audits_api_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A failure on the SDK call writes an api_error audit event AND re-raises."""
    class _ErrWithRequestId(RuntimeError):
        request_id: str

    def boom(*_a: object, **_kw: object) -> object:
        e = _ErrWithRequestId("server said 500")
        e.request_id = "req-9"
        raise e

    monkeypatch.setattr(layer2.mutate_impl, "mutate", boom)  # type: ignore[attr-defined]

    audit = _RecordingAudit()
    op = Operation(
        service="campaign",
        op="update",
        resource={"resource_name": "customers/1/campaigns/2", "status": "PAUSED"},
        update_mask=["status"],
    )

    with pytest.raises(_ErrWithRequestId, match="server said 500"):
        layer2._apply_operations(
            "1234567890", [op],
            mutate_id="abc", client=object(), audit=audit,
        )

    assert len(audit.events) == 1
    e = audit.events[0]
    assert e.outcome == "api_error"
    assert e.payload_kind == "operations"
    assert e.error_type == "_ErrWithRequestId"
    assert e.error_request_id == "req-9"


def test_apply_rpc_call_audits_ok(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The rpc_call path dispatches via rpc_impl.invoke with validate_only=False,
    extracts resource_names, and writes a single 'apply ok' audit event with
    payload_kind=rpc_call."""
    invoke_calls: list[tuple[str, str, dict[str, object], object]] = []

    def fake_invoke(
        _client: object, service: str, method: str, params: dict[str, object],
        *, customer_id: object, validate_only: object,
    ) -> object:
        invoke_calls.append((service, method, params, validate_only))
        # Returned object will go through _proto.message_to_dict; simulate it.
        return _msg({"results": [{"resource_name": "customers/1/recommendations/abc"}]})

    monkeypatch.setattr(layer2.rpc_impl, "invoke", fake_invoke)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        layer2._proto,  # type: ignore[attr-defined]
        "message_to_dict",
        lambda m: m._dict,
    )

    audit = _RecordingAudit()
    rpc = RpcCall(
        service="recommendation_service",
        method="apply_recommendation",
        params={"resource_name": "customers/1/recommendations/abc"},
    )

    result = layer2._apply_rpc_call(
        "1234567890", rpc,
        mutate_id="r-1", client=object(), audit=audit,
    )

    # Dispatched once with validate_only=False.
    assert len(invoke_calls) == 1
    _, _, _, validate_only = invoke_calls[0]
    assert validate_only is False

    assert result.applied is True
    assert result.resource_names == ["customers/1/recommendations/abc"]

    assert len(audit.events) == 1
    e = audit.events[0]
    assert e.phase == "apply"
    assert e.outcome == "ok"
    assert e.payload_kind == "rpc_call"
    assert e.operations is None
    assert e.rpc_call == rpc


def test_apply_rpc_call_audits_api_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def boom(*_a: object, **_kw: object) -> object:
        raise RuntimeError("rpc nope")

    monkeypatch.setattr(layer2.rpc_impl, "invoke", boom)  # type: ignore[attr-defined]

    audit = _RecordingAudit()
    rpc = RpcCall(
        service="recommendation_service",
        method="apply_recommendation",
        params={"resource_name": "x"},
    )

    with pytest.raises(RuntimeError, match="rpc nope"):
        layer2._apply_rpc_call(
            "1234567890", rpc,
            mutate_id="r-1", client=object(), audit=audit,
        )

    assert audit.events[0].outcome == "api_error"
    assert audit.events[0].payload_kind == "rpc_call"
    assert audit.events[0].rpc_call == rpc
