"""Tests for the JSONL audit logger.

Schema is shared with `observability/audit.py`'s docstring; tests pin the
exact wire format (timestamp, phase, outcome, payload-kind blocks, error
block) since downstream greppers / log shippers depend on it.
"""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

from google_ads_mcp.observability.audit import AuditEvent, JsonlAuditLogger
from google_ads_mcp.types import Operation, RpcCall


class _FixedClock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def now(self) -> datetime:
        return self.t


def _op() -> Operation:
    return Operation(
        service="campaign",
        op="update",
        resource={"resource_name": "customers/1/campaigns/2", "status": "PAUSED"},
        update_mask=["status"],
    )


def _ok_event(mutate_id: str = "abc-123") -> AuditEvent:
    return AuditEvent(
        phase="apply",
        outcome="ok",
        mutate_id=mutate_id,
        customer_id="1234567890",
        payload_kind="operations",
        operations=[_op()],
        rpc_call=None,
        resource_names=["customers/1234567890/campaigns/2"],
        error_type=None,
        error_message=None,
        error_request_id=None,
    )


def test_writes_ok_event(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    clock = _FixedClock(datetime(2026, 4, 28, 18, 30, 15, tzinfo=UTC))
    logger = JsonlAuditLogger(path=log_path, clock=clock)

    logger.record(_ok_event())

    lines = log_path.read_text().splitlines()
    assert len(lines) == 1

    entry = json.loads(lines[0])
    assert entry["phase"] == "apply"
    assert entry["outcome"] == "ok"
    assert entry["mutate_id"] == "abc-123"
    assert entry["customer_id"] == "1234567890"
    assert entry["payload_kind"] == "operations"
    assert entry["timestamp"] == "2026-04-28T18:30:15+00:00"
    assert entry["result"] == {"resource_names": ["customers/1234567890/campaigns/2"]}
    assert entry["error"] is None
    assert entry["rpc_call"] is None
    assert len(entry["operations"]) == 1
    assert entry["operations"][0]["service"] == "campaign"


def test_writes_error_event(tmp_path: Path) -> None:
    """Failure-path events carry full error context for forensics."""
    log_path = tmp_path / "audit.log"
    logger = JsonlAuditLogger(
        path=log_path, clock=_FixedClock(datetime(2026, 4, 28, tzinfo=UTC))
    )

    logger.record(
        AuditEvent(
            phase="preview",
            outcome="guardrail_rejection",
            mutate_id=None,  # never assigned — guardrail tripped before pending.store
            customer_id="1234567890",
            payload_kind="operations",
            operations=[_op()],
            rpc_call=None,
            resource_names=None,
            error_type="GuardrailViolation",
            error_message="customer_id '9876543210' is not accessible.",
            error_request_id=None,
        )
    )

    entry = json.loads(log_path.read_text().splitlines()[0])
    assert entry["outcome"] == "guardrail_rejection"
    assert entry["mutate_id"] is None
    assert entry["payload_kind"] == "operations"
    assert entry["result"] is None
    assert entry["error"]["type"] == "GuardrailViolation"
    assert "is not accessible" in entry["error"]["message"]


def test_writes_rpc_call_event(tmp_path: Path) -> None:
    """RPC-kind events serialize the rpc_call field, not operations."""
    log_path = tmp_path / "audit.log"
    logger = JsonlAuditLogger(
        path=log_path, clock=_FixedClock(datetime(2026, 4, 28, tzinfo=UTC))
    )

    logger.record(
        AuditEvent(
            phase="preview",
            outcome="ok",
            mutate_id="rpc-1",
            customer_id="1234567890",
            payload_kind="rpc_call",
            operations=None,
            rpc_call=RpcCall(
                service="recommendation_service",
                method="apply_recommendation",
                params={"resource_name": "customers/1234567890/recommendations/abc"},
            ),
            resource_names=None,
            error_type=None,
            error_message=None,
            error_request_id=None,
        )
    )

    entry = json.loads(log_path.read_text().splitlines()[0])
    assert entry["payload_kind"] == "rpc_call"
    assert entry["operations"] is None
    assert entry["rpc_call"]["service"] == "recommendation_service"
    assert entry["rpc_call"]["method"] == "apply_recommendation"
    assert (
        entry["rpc_call"]["params"]["resource_name"]
        == "customers/1234567890/recommendations/abc"
    )


def test_appends_subsequent_calls(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    logger = JsonlAuditLogger(
        path=log_path,
        clock=_FixedClock(datetime(2026, 4, 28, tzinfo=UTC)),
    )

    for i in range(3):
        logger.record(_ok_event(mutate_id=f"id-{i}"))

    assert len(log_path.read_text().splitlines()) == 3


def test_creates_parent_directory(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "subdir" / "audit.log"
    logger = JsonlAuditLogger(
        path=log_path,
        clock=_FixedClock(datetime(2026, 4, 28, tzinfo=UTC)),
    )

    logger.record(_ok_event())

    assert log_path.is_file()


def test_sets_mode_0600(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    logger = JsonlAuditLogger(
        path=log_path,
        clock=_FixedClock(datetime(2026, 4, 28, tzinfo=UTC)),
    )

    logger.record(_ok_event())

    mode = stat.S_IMODE(log_path.stat().st_mode)
    assert mode == 0o600
