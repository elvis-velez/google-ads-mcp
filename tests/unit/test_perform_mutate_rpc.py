# pyright: reportUnknownMemberType=false, reportUnknownLambdaType=false, reportUnknownVariableType=false
"""Tests for tools._flow.perform_mutate_rpc.

Mirrors test_pending / test_audit patterns: feed in a mock SDK client and
real allowlist/pending/audit collaborators, drive the flow, assert audit
entries and the returned MutatePreview.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from google_ads_mcp.errors import ApiError, GuardrailViolation, ValidationFailed
from google_ads_mcp.observability.audit import AuditEvent
from google_ads_mcp.safety.allowlist import CustomerAllowlist
from google_ads_mcp.safety.pending import PendingStore
from google_ads_mcp.tools._flow import perform_mutate_rpc
from google_ads_mcp.types import RpcCall


class _FixedClock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def now(self) -> datetime:
        return self.t


class _RecordingAudit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)


def _store(clock: _FixedClock) -> PendingStore:
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"id-{counter}"

    return PendingStore(clock=clock, ttl=timedelta(seconds=900), id_factory=next_id)


def _allowlist(allowed: tuple[str, ...]) -> CustomerAllowlist:
    return CustomerAllowlist(fetch=lambda: list(allowed))


def _rpc() -> RpcCall:
    return RpcCall(
        service="recommendation_service",
        method="apply_recommendation",
        params={"resource_name": "customers/1234567890/recommendations/abc"},
    )


def test_perform_mutate_rpc_skips_validate_when_unsupported(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When the request type doesn't have validate_only, the preview is
    client-side only — no API round-trip during the preview phase."""
    invoke_calls: list[tuple[str, str, dict[str, object], object]] = []

    def fake_invoke(
        client: object, service: str, method: str, params: dict[str, object],
        *, customer_id: object, validate_only: object,
    ) -> object:
        invoke_calls.append((service, method, params, validate_only))
        return object()

    monkeypatch.setattr("google_ads_mcp.tools._flow.rpc_impl.invoke", fake_invoke)

    audit = _RecordingAudit()
    preview = perform_mutate_rpc(
        client=object(),
        customer_id="1234567890",
        rpc_call=_rpc(),
        supports_validate_only=False,
        allowlist=_allowlist(("1234567890",)),
        pending=_store(_FixedClock(datetime(2026, 4, 28, tzinfo=UTC))),
        audit=audit,
    )

    assert invoke_calls == []  # no API round-trip
    assert preview.mutate_id == "id-1"
    assert preview.operations_count == 1
    assert len(preview.diffs) == 1
    assert preview.diffs[0].kind == "rpc_call"

    assert len(audit.events) == 1
    assert audit.events[0].phase == "preview"
    assert audit.events[0].outcome == "ok"
    assert audit.events[0].payload_kind == "rpc_call"
    assert audit.events[0].rpc_call is not None
    assert audit.events[0].rpc_call.method == "apply_recommendation"


def test_perform_mutate_rpc_round_trips_when_supported(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When the request type has validate_only, the preview round-trips with
    validate_only=True so the API gets a chance to reject the call."""
    invoke_calls: list[tuple[str, str, dict[str, object], object]] = []

    def fake_invoke(
        client: object, service: str, method: str, params: dict[str, object],
        *, customer_id: object, validate_only: object,
    ) -> object:
        invoke_calls.append((service, method, params, validate_only))
        return object()

    monkeypatch.setattr("google_ads_mcp.tools._flow.rpc_impl.invoke", fake_invoke)

    audit = _RecordingAudit()
    perform_mutate_rpc(
        client=object(),
        customer_id="1234567890",
        rpc_call=RpcCall(
            service="experiment_service",
            method="promote_experiment",
            params={"resource_name": "customers/1234567890/experiments/9"},
        ),
        supports_validate_only=True,
        allowlist=_allowlist(("1234567890",)),
        pending=_store(_FixedClock(datetime(2026, 4, 28, tzinfo=UTC))),
        audit=audit,
    )

    assert len(invoke_calls) == 1
    service, method, _params, validate_only = invoke_calls[0]
    assert service == "experiment_service"
    assert method == "promote_experiment"
    assert validate_only is True
    # The audit entry is still the preview-ok one; no separate API audit.
    assert [e.outcome for e in audit.events] == ["ok"]


def test_perform_mutate_rpc_audits_allowlist_rejection() -> None:
    audit = _RecordingAudit()

    with pytest.raises(GuardrailViolation):
        perform_mutate_rpc(
            client=object(),
            customer_id="9999999999",
            rpc_call=_rpc(),
            supports_validate_only=False,
            allowlist=_allowlist(("1234567890",)),
            pending=_store(_FixedClock(datetime(2026, 4, 28, tzinfo=UTC))),
            audit=audit,
        )

    assert len(audit.events) == 1
    e = audit.events[0]
    assert e.outcome == "guardrail_rejection"
    assert e.payload_kind == "rpc_call"
    assert e.rpc_call is not None
    assert e.error_type == "GuardrailViolation"
    # No mutate_id on a pre-store rejection.
    assert e.mutate_id is None


def test_perform_mutate_rpc_audits_validation_failure(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A validate_only round-trip that the API rejects surfaces as a
    validation_failed audit entry, with the same error text from the
    underlying ValidationFailed."""

    def fake_invoke(*_args: object, **_kw: object) -> object:
        raise ValidationFailed("operation requires update_mask")

    monkeypatch.setattr("google_ads_mcp.tools._flow.rpc_impl.invoke", fake_invoke)

    audit = _RecordingAudit()
    with pytest.raises(ValidationFailed, match="update_mask"):
        perform_mutate_rpc(
            client=object(),
            customer_id="1234567890",
            rpc_call=_rpc(),
            supports_validate_only=True,
            allowlist=_allowlist(("1234567890",)),
            pending=_store(_FixedClock(datetime(2026, 4, 28, tzinfo=UTC))),
            audit=audit,
        )

    assert len(audit.events) == 1
    assert audit.events[0].outcome == "validation_failed"
    assert audit.events[0].error_type == "ValidationFailed"


def test_perform_mutate_rpc_audits_api_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fake_invoke(*_args: object, **_kw: object) -> object:
        raise ApiError("API said no", request_id="abc-123")

    monkeypatch.setattr("google_ads_mcp.tools._flow.rpc_impl.invoke", fake_invoke)

    audit = _RecordingAudit()
    with pytest.raises(ApiError):
        perform_mutate_rpc(
            client=object(),
            customer_id="1234567890",
            rpc_call=_rpc(),
            supports_validate_only=True,
            allowlist=_allowlist(("1234567890",)),
            pending=_store(_FixedClock(datetime(2026, 4, 28, tzinfo=UTC))),
            audit=audit,
        )

    assert audit.events[0].outcome == "api_error"
    assert audit.events[0].error_request_id == "abc-123"


def test_perform_mutate_rpc_stores_pending_payload() -> None:
    """The mutate_id returned in the preview must be addressable by a
    subsequent apply call — the store contract end of the flow."""
    audit = _RecordingAudit()
    pending = _store(_FixedClock(datetime(2026, 4, 28, tzinfo=UTC)))

    rpc = _rpc()
    preview = perform_mutate_rpc(
        client=object(),
        customer_id="1234567890",
        rpc_call=rpc,
        supports_validate_only=False,
        allowlist=_allowlist(("1234567890",)),
        pending=pending,
        audit=audit,
    )

    seen_payload: dict[str, object] = {}

    def applier(customer_id: str, payload: object) -> object:
        seen_payload["customer_id"] = customer_id
        seen_payload["payload"] = payload
        # We don't actually mutate; just verify dispatch.
        from google_ads_mcp.types import ApplyResult

        return ApplyResult(
            mutate_id="placeholder",
            customer_id=customer_id,
            applied=True,
            resource_names=[],
        )

    pending.apply(preview.mutate_id, applier)  # type: ignore[arg-type]

    assert seen_payload["customer_id"] == "1234567890"
    payload = seen_payload["payload"]
    # Must be the RpcCallPayload variant carrying our RpcCall.
    assert getattr(payload, "kind", None) == "rpc_call"
    assert getattr(payload, "rpc_call", None) == rpc


