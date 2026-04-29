"""Mutation audit log.

Append-only JSONL at `~/.local/share/google-ads-mcp/audit.log` (mode 0600).
Records every state-changing *attempt* — successful, rejected by a
guardrail, rejected by API validation, rejected for an expired mutate_id,
or returning a cached result. The point is forensic: any "did the LLM do
X?" question is answerable by `grep`-ing one file.

Schema (one JSON object per line):

    {
      "timestamp": "2026-04-28T18:30:15.123456+00:00",
      "phase":     "preview" | "apply",
      "outcome":   "ok" | "guardrail_rejection" | "validation_failed"
                   | "api_error" | "expired" | "not_found" | "cached_replay",
      "mutate_id": "..." | null,
      "customer_id": "1234567890" | null,
      "payload_kind": "operations" | "rpc_call" | null,
      "operations": [{...}, ...] | null,                            # payload_kind=operations
      "rpc_call":   {"service":..., "method":..., "params":...} | null,   # payload_kind=rpc_call
      "result":     {"resource_names": [...]} | null,
      "error":      {"type": "...", "message": "...", "request_id": "..." | null} | null
    }

`payload_kind` discriminates which of `operations`/`rpc_call` is populated.
Failure-path entries always include enough context to act on:
- `mutate_id` if known (apply-time failures always; preview-time guardrail
  failures don't have one yet).
- The relevant payload (operations or rpc_call) whenever it was resolvable.
- `error.type` is the Python class name; `error.message` is human-readable.

POSIX append writes are atomic up to PIPE_BUF (4096 bytes), well above any
realistic entry size, so concurrent writers from different processes don't
interleave.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from google_ads_mcp.observability.clock import Clock
from google_ads_mcp.types import Operation, RpcCall

Phase = Literal["preview", "apply"]
Outcome = Literal[
    "ok",
    "guardrail_rejection",
    "validation_failed",
    "api_error",
    "expired",
    "not_found",
    "cached_replay",
]
PayloadKind = Literal["operations", "rpc_call"]


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One line of the audit log, before serialization.

    `payload_kind` discriminates which of `operations` / `rpc_call` is
    populated. Both may be None (e.g. apply-time not_found, where the entry
    was missing so we never knew the kind).
    """

    phase: Phase
    outcome: Outcome
    mutate_id: str | None
    customer_id: str | None
    payload_kind: PayloadKind | None
    operations: list[Operation] | None
    rpc_call: RpcCall | None
    resource_names: list[str] | None
    error_type: str | None
    error_message: str | None
    error_request_id: str | None


class AuditLogger(Protocol):
    """Records mutation events. Injected for testability."""

    def record(self, event: AuditEvent) -> None: ...


class JsonlAuditLogger:
    """Default impl: append-only JSONL to a file with mode 0600."""

    def __init__(self, *, path: Path, clock: Clock) -> None:
        self._path = path
        self._clock = clock

    def record(self, event: AuditEvent) -> None:
        entry: dict[str, Any] = {
            "timestamp": self._clock.now().isoformat(),
            "phase": event.phase,
            "outcome": event.outcome,
            "mutate_id": event.mutate_id,
            "customer_id": event.customer_id,
            "payload_kind": event.payload_kind,
            "operations": (
                [op.model_dump() for op in event.operations]
                if event.operations is not None
                else None
            ),
            "rpc_call": (
                event.rpc_call.model_dump() if event.rpc_call is not None else None
            ),
            "result": (
                {"resource_names": event.resource_names}
                if event.resource_names is not None
                else None
            ),
            "error": (
                {
                    "type": event.error_type,
                    "message": event.error_message,
                    "request_id": event.error_request_id,
                }
                if event.error_type is not None
                else None
            ),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line)
        # Tighten permissions on first creation; chmod is idempotent. Best-effort
        # — even if it fails, the file is on the operator's user-owned filesystem.
        with contextlib.suppress(OSError):
            self._path.chmod(0o600)
