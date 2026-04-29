"""Tests for the TTL'd, idempotent pending-mutate store."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from google_ads_mcp.errors import PendingExpired, PendingNotFound
from google_ads_mcp.safety.pending import PendingStore
from google_ads_mcp.types import (
    ApplyResult,
    Operation,
    OperationsPayload,
    PendingPayload,
    RpcCall,
    RpcCallPayload,
)


class _FixedClock:
    def __init__(self, start: datetime) -> None:
        self.t = start

    def now(self) -> datetime:
        return self.t


def _ops_payload() -> OperationsPayload:
    return OperationsPayload(
        operations=[
            Operation(
                service="campaign",
                op="update",
                resource={"resource_name": "customers/1/campaigns/2", "status": "PAUSED"},
                update_mask=["status"],
            ),
        ],
    )


def _rpc_payload() -> RpcCallPayload:
    return RpcCallPayload(
        rpc_call=RpcCall(
            service="recommendation_service",
            method="apply_recommendation",
            params={"resource_name": "customers/1/recommendations/abc"},
        ),
    )


def _applier_returning(resource_names: list[str]) -> Callable[..., ApplyResult]:
    def go(customer_id: str, _payload: PendingPayload) -> ApplyResult:
        return ApplyResult(
            mutate_id="placeholder",
            customer_id=customer_id,
            applied=True,
            resource_names=resource_names,
        )

    return go


def _store(clock: _FixedClock, ttl_seconds: int = 900) -> PendingStore:
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"id-{counter}"

    return PendingStore(
        clock=clock,
        ttl=timedelta(seconds=ttl_seconds),
        id_factory=next_id,
    )


def test_store_returns_id_and_expiry() -> None:
    clock = _FixedClock(datetime(2026, 4, 28, tzinfo=UTC))
    store = _store(clock, ttl_seconds=600)

    mutate_id, expires_at = store.store(
        customer_id="1234567890", payload=_ops_payload()
    )

    assert mutate_id == "id-1"
    assert expires_at == clock.t + timedelta(seconds=600)
    assert len(store) == 1


def test_apply_runs_applier_once() -> None:
    clock = _FixedClock(datetime(2026, 4, 28, tzinfo=UTC))
    store = _store(clock)
    mutate_id, _ = store.store(customer_id="1234567890", payload=_ops_payload())

    calls = 0

    def applier(customer_id: str, _payload: PendingPayload) -> ApplyResult:
        nonlocal calls
        calls += 1
        return ApplyResult(
            mutate_id="placeholder",
            customer_id=customer_id,
            applied=True,
            resource_names=["customers/1234567890/campaigns/2"],
        )

    first = store.apply(mutate_id, applier)
    second = store.apply(mutate_id, applier)

    assert calls == 1
    assert first.applied is True
    assert second.applied is False  # re-apply returns cached result, didn't re-mutate
    assert first.resource_names == second.resource_names == ["customers/1234567890/campaigns/2"]


def test_unknown_mutate_id_raises() -> None:
    clock = _FixedClock(datetime(2026, 4, 28, tzinfo=UTC))
    store = _store(clock)

    with pytest.raises(PendingNotFound, match="not found"):
        store.apply("never-stored", _applier_returning([]))


def test_expired_mutate_id_raises_and_evicts() -> None:
    clock = _FixedClock(datetime(2026, 4, 28, tzinfo=UTC))
    store = _store(clock, ttl_seconds=60)
    mutate_id, _ = store.store(customer_id="1234567890", payload=_ops_payload())

    clock.t += timedelta(seconds=120)  # well past TTL

    with pytest.raises(PendingExpired, match="expired"):
        store.apply(mutate_id, _applier_returning([]))

    # Subsequent attempts should now report not-found, not expired (the entry
    # was evicted on the expired access).
    assert len(store) == 0
    with pytest.raises(PendingNotFound):
        store.apply(mutate_id, _applier_returning([]))


def test_passes_customer_and_payload_to_applier() -> None:
    clock = _FixedClock(datetime(2026, 4, 28, tzinfo=UTC))
    store = _store(clock)
    payload = _ops_payload()
    mutate_id, _ = store.store(customer_id="9999999999", payload=payload)

    seen: dict[str, object] = {}

    def applier(customer_id: str, p: PendingPayload) -> ApplyResult:
        seen["customer_id"] = customer_id
        seen["payload"] = p
        return ApplyResult(
            mutate_id="placeholder",
            customer_id=customer_id,
            applied=True,
            resource_names=[],
        )

    store.apply(mutate_id, applier)

    assert seen["customer_id"] == "9999999999"
    assert seen["payload"] == payload


def test_rpc_call_payload_round_trips() -> None:
    """The store treats RpcCall payloads identically to Operations — same TTL,
    same idempotency, applier dispatches on payload kind."""
    clock = _FixedClock(datetime(2026, 4, 28, tzinfo=UTC))
    store = _store(clock)
    payload = _rpc_payload()
    mutate_id, _ = store.store(customer_id="1234567890", payload=payload)

    seen_kind: list[str] = []

    def applier(customer_id: str, p: PendingPayload) -> ApplyResult:
        seen_kind.append(p.kind)
        return ApplyResult(
            mutate_id="placeholder",
            customer_id=customer_id,
            applied=True,
            resource_names=["customers/1234567890/recommendations/abc"],
        )

    first = store.apply(mutate_id, applier)
    second = store.apply(mutate_id, applier)

    assert seen_kind == ["rpc_call"]  # applier ran exactly once
    assert first.applied is True
    assert second.applied is False
