"""Audit log writer.

Append-only JSONL at `~/.local/share/google-ads-mcp/audit.log` (mode 0600).
Records every successful `apply`. The append-only contract means readers
can `tail -f` safely and never see partial entries.

Schema (one JSON object per line):

    {
      "timestamp": "2026-04-28T18:30:15.123456+00:00",
      "mutate_id": "...",
      "customer_id": "1234567890",
      "operations": [{"service": "...", "op": "...", "resource": {...}, ...}, ...],
      "resource_names": ["customers/.../campaigns/...", ...]
    }

Failure-path audits (validation rejections, guardrail violations, API errors
mid-apply) are not written in v1 — they show up in stderr/MCP logs and the
LLM surfaces them. Add when an operator workflow needs them.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Protocol

from google_ads_mcp.safety.clock import Clock
from google_ads_mcp.types import Operation


class AuditLogger(Protocol):
    """Records committed mutations. Injected for testability."""

    def log_apply(
        self,
        *,
        mutate_id: str,
        customer_id: str,
        operations: list[Operation],
        resource_names: list[str],
    ) -> None: ...


class JsonlAuditLogger:
    """Default impl: append-only JSONL to a file with mode 0600."""

    def __init__(self, *, path: Path, clock: Clock) -> None:
        self._path = path
        self._clock = clock

    def log_apply(
        self,
        *,
        mutate_id: str,
        customer_id: str,
        operations: list[Operation],
        resource_names: list[str],
    ) -> None:
        entry: dict[str, Any] = {
            "timestamp": self._clock.now().isoformat(),
            "mutate_id": mutate_id,
            "customer_id": customer_id,
            "operations": [op.model_dump() for op in operations],
            "resource_names": resource_names,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # `a` opens append-mode; on POSIX, append writes are atomic up to PIPE_BUF
        # (4096 bytes) — well above a typical entry, so concurrent appends from
        # different processes don't interleave.
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line)
        # Tighten permissions on first creation; chmod is idempotent. Best-effort
        # — even if it fails, the file is on the operator's user-owned filesystem.
        with contextlib.suppress(OSError):
            self._path.chmod(0o600)
