"""Tests for the JSONL audit logger."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

from google_ads_mcp.safety.audit import JsonlAuditLogger
from google_ads_mcp.types import Operation


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


def test_writes_one_jsonl_line(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    clock = _FixedClock(datetime(2026, 4, 28, 18, 30, 15, tzinfo=UTC))
    logger = JsonlAuditLogger(path=log_path, clock=clock)

    logger.log_apply(
        mutate_id="abc-123",
        customer_id="1234567890",
        operations=[_op()],
        resource_names=["customers/1234567890/campaigns/2"],
    )

    lines = log_path.read_text().splitlines()
    assert len(lines) == 1

    entry = json.loads(lines[0])
    assert entry["mutate_id"] == "abc-123"
    assert entry["customer_id"] == "1234567890"
    assert entry["timestamp"] == "2026-04-28T18:30:15+00:00"
    assert entry["resource_names"] == ["customers/1234567890/campaigns/2"]
    assert len(entry["operations"]) == 1
    assert entry["operations"][0]["service"] == "campaign"
    assert entry["operations"][0]["op"] == "update"


def test_appends_subsequent_calls(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    logger = JsonlAuditLogger(
        path=log_path,
        clock=_FixedClock(datetime(2026, 4, 28, tzinfo=UTC)),
    )

    for i in range(3):
        logger.log_apply(
            mutate_id=f"id-{i}",
            customer_id="1234567890",
            operations=[_op()],
            resource_names=[],
        )

    assert len(log_path.read_text().splitlines()) == 3


def test_creates_parent_directory(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "subdir" / "audit.log"
    logger = JsonlAuditLogger(
        path=log_path,
        clock=_FixedClock(datetime(2026, 4, 28, tzinfo=UTC)),
    )

    logger.log_apply(
        mutate_id="x", customer_id="1", operations=[_op()], resource_names=[]
    )

    assert log_path.is_file()


def test_sets_mode_0600(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.log"
    logger = JsonlAuditLogger(
        path=log_path,
        clock=_FixedClock(datetime(2026, 4, 28, tzinfo=UTC)),
    )

    logger.log_apply(
        mutate_id="x", customer_id="1", operations=[_op()], resource_names=[]
    )

    mode = stat.S_IMODE(log_path.stat().st_mode)
    assert mode == 0o600
