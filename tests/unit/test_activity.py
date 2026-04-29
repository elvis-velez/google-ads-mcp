"""Tests for activity logging."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from google_ads_mcp.observability.activity import (
    ActivityEvent,
    ActivityRecorder,
    JsonlActivityLogger,
    summarize_args,
    with_activity,
)


class _AdvanceableClock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def now(self) -> datetime:
        return self.t

    def advance(self, ms: int) -> None:
        self.t += timedelta(milliseconds=ms)


def _ok_event() -> ActivityEvent:
    return ActivityEvent(
        kind="tool",
        name="pause_campaign",
        args_summary={"customer_id": "1234567890", "campaign_id": "1"},
        duration_ms=42,
        outcome="ok",
        error_type=None,
        error_message=None,
    )


# --- JsonlActivityLogger ----------------------------------------------------


def test_writes_jsonl_line(tmp_path: Path) -> None:
    log_path = tmp_path / "activity.log"
    clock = _AdvanceableClock(datetime(2026, 4, 28, 18, 30, 15, tzinfo=UTC))
    logger = JsonlActivityLogger(path=log_path, clock=clock)

    logger.record(_ok_event())

    entry = json.loads(log_path.read_text().splitlines()[0])
    assert entry["kind"] == "tool"
    assert entry["name"] == "pause_campaign"
    assert entry["outcome"] == "ok"
    assert entry["duration_ms"] == 42
    assert entry["error"] is None


def test_failure_writes_dont_break_callers(tmp_path: Path) -> None:
    """If the activity log can't be written (read-only fs etc.), the tool
    call must not fail. Writes are best-effort."""
    log_path = tmp_path / "ro" / "activity.log"
    log_path.parent.mkdir()
    log_path.parent.chmod(stat.S_IRUSR | stat.S_IXUSR)  # read-only

    logger = JsonlActivityLogger(
        path=log_path, clock=_AdvanceableClock(datetime(2026, 4, 28, tzinfo=UTC))
    )

    try:
        logger.record(_ok_event())  # must not raise
    finally:
        log_path.parent.chmod(0o700)


# --- ActivityRecorder context manager ---------------------------------------


def test_record_call_captures_duration_and_ok(tmp_path: Path) -> None:
    log_path = tmp_path / "activity.log"
    clock = _AdvanceableClock(datetime(2026, 4, 28, tzinfo=UTC))
    recorder = ActivityRecorder(
        logger=JsonlActivityLogger(path=log_path, clock=clock),
        clock=clock,
    )

    args = {"customer_id": "1", "query": "SELECT campaign.id FROM campaign"}
    with recorder.record_call(kind="tool", name="gaql", args=args):
        clock.advance(150)

    entry = json.loads(log_path.read_text().splitlines()[0])
    assert entry["outcome"] == "ok"
    assert entry["duration_ms"] == 150
    assert entry["args_summary"]["customer_id"] == "1"


def test_record_call_captures_error_and_propagates(tmp_path: Path) -> None:
    log_path = tmp_path / "activity.log"
    clock = _AdvanceableClock(datetime(2026, 4, 28, tzinfo=UTC))
    recorder = ActivityRecorder(
        logger=JsonlActivityLogger(path=log_path, clock=clock),
        clock=clock,
    )

    with (
        pytest.raises(RuntimeError, match="boom"),
        recorder.record_call(kind="tool", name="apply", args={"mutate_id": "x"}),
    ):
        clock.advance(50)
        raise RuntimeError("boom")

    entry = json.loads(log_path.read_text().splitlines()[0])
    assert entry["outcome"] == "error"
    assert entry["error"]["type"] == "RuntimeError"
    assert "boom" in entry["error"]["message"]
    assert entry["duration_ms"] == 50


# --- summarize_args ---------------------------------------------------------


class _Model(BaseModel):
    x: int


def test_summarize_truncates_long_strings() -> None:
    long_query = "SELECT " + "x," * 200 + " FROM y"
    out = summarize_args({"query": long_query})

    assert "...[truncated" in out["query"]
    assert len(out["query"]) < len(long_query)


def test_summarize_collapses_lists() -> None:
    out = summarize_args({"operations": [object(), object(), object()]})
    assert out["operations"] == "<list: 3 item(s)>"


def test_summarize_hides_pydantic_models() -> None:
    out = summarize_args({"op": _Model(x=1)})
    assert out["op"] == "<_Model>"


def test_summarize_passes_primitives() -> None:
    out = summarize_args({"customer_id": "1234567890", "n": 42, "ok": True})
    assert out == {"customer_id": "1234567890", "n": 42, "ok": True}


# --- with_activity decorator ------------------------------------------------


def test_decorator_preserves_signature_via_wraps() -> None:
    """FastMCP introspects the original signature via __wrapped__ — the
    decorator must preserve it so tool schemas are correctly generated."""
    import inspect

    clock = _AdvanceableClock(datetime(2026, 4, 28, tzinfo=UTC))
    recorder = ActivityRecorder(
        logger=JsonlActivityLogger(path=Path("/dev/null"), clock=clock),
        clock=clock,
    )

    async def original(customer_id: str, campaign_id: str) -> str:
        return f"{customer_id}/{campaign_id}"

    wrapped = with_activity(recorder, name="x")(original)

    sig = inspect.signature(wrapped)
    assert list(sig.parameters) == ["customer_id", "campaign_id"]
