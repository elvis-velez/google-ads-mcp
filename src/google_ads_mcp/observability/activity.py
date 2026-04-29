"""Activity log: every tool/resource call.

Append-only JSONL at `~/.local/share/google-ads-mcp/activity.log`. Lower-
stakes than the audit log — reads are included, schema is wider, rotation
is fine. Useful for debugging "what did the LLM ask yesterday?" and for
spotting performance regressions in tool calls.

Schema (one JSON object per line):

    {
      "timestamp":    "...",
      "kind":         "tool" | "resource",
      "name":         "pause_campaign" | "gaql" | ...,
      "args_summary": {...},      // sanitized: long strings truncated, lists summarized
      "duration_ms":  123,
      "outcome":      "ok" | "error",
      "error":        {"type": "...", "message": "..."} | null
    }

The `record_call` context manager wraps a single tool/resource handler.
Use the `with_activity` decorator factory at registration sites to apply
it uniformly without per-tool boilerplate.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import json
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar, cast

from pydantic import BaseModel

from google_ads_mcp.observability.clock import Clock

ActivityKind = Literal["tool", "resource"]


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    kind: ActivityKind
    name: str
    args_summary: dict[str, Any]
    duration_ms: int
    outcome: Literal["ok", "error"]
    error_type: str | None
    error_message: str | None


class ActivityLogger(Protocol):
    def record(self, event: ActivityEvent) -> None: ...


class JsonlActivityLogger:
    """Default impl: append-only JSONL. Best-effort write — failures during
    logging never block the underlying tool call."""

    def __init__(self, *, path: Path, clock: Clock) -> None:
        self._path = path
        self._clock = clock

    def record(self, event: ActivityEvent) -> None:
        entry: dict[str, Any] = {
            "timestamp": self._clock.now().isoformat(),
            "kind": event.kind,
            "name": event.name,
            "args_summary": event.args_summary,
            "duration_ms": event.duration_ms,
            "outcome": event.outcome,
            "error": (
                {"type": event.error_type, "message": event.error_message}
                if event.error_type is not None
                else None
            ),
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, separators=(",", ":"), default=str) + "\n"
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            # Activity logging is non-critical: don't break a tool call because
            # the disk filled up. Diagnostics will pick up the OSError if the
            # operator has stderr logging enabled.
            return


class ActivityRecorder:
    """Wraps an ActivityLogger with a context manager that captures duration."""

    def __init__(self, *, logger: ActivityLogger, clock: Clock) -> None:
        self._logger = logger
        self._clock = clock

    @contextlib.contextmanager
    def record_call(
        self, *, kind: ActivityKind, name: str, args: dict[str, Any]
    ) -> Generator[None]:
        start = self._clock.now()
        try:
            yield
        except Exception as e:
            elapsed = self._clock.now() - start
            self._logger.record(
                ActivityEvent(
                    kind=kind,
                    name=name,
                    args_summary=summarize_args(args),
                    duration_ms=_to_ms(elapsed),
                    outcome="error",
                    error_type=type(e).__name__,
                    error_message=_truncate(str(e)),
                )
            )
            raise
        else:
            elapsed = self._clock.now() - start
            self._logger.record(
                ActivityEvent(
                    kind=kind,
                    name=name,
                    args_summary=summarize_args(args),
                    duration_ms=_to_ms(elapsed),
                    outcome="ok",
                    error_type=None,
                    error_message=None,
                )
            )


_HandlerT = TypeVar("_HandlerT", bound=Callable[..., Awaitable[Any]])


def with_activity(
    recorder: ActivityRecorder,
    *,
    name: str,
    kind: ActivityKind = "tool",
) -> Callable[[_HandlerT], _HandlerT]:
    """Decorator factory: wraps an async handler with activity recording.

    Usage at registration sites — the wrapped function preserves the
    original signature via `functools.wraps`, so FastMCP's signature-based
    schema generation still sees the real parameters.

    If `name` contains `{...}` placeholders (e.g. URI templates for
    parameterized resources), they're rendered against the call's args
    so the activity log shows `gads-schema://campaign` instead of the bare
    template. We bind positional args back to parameter names via
    `inspect.signature` because FastMCP passes URI-template variables to
    resource handlers positionally, not as kwargs — `name.format(**kwargs)`
    alone would always fall back to the literal template for resources.
    Falls back to the literal `name` if a placeholder isn't supplied —
    never breaks a tool call to log nicer text.
    """
    is_template = "{" in name

    def decorator(handler: _HandlerT) -> _HandlerT:
        # Resolve the handler's signature once at decoration time; binding at
        # call time is then cheap.
        signature = inspect.signature(handler) if is_template else None

        @functools.wraps(handler)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            full_args: dict[str, Any] = dict(kwargs)
            if is_template and signature is not None:
                try:
                    bound = signature.bind(*args, **kwargs)
                    bound.apply_defaults()
                    full_args = dict(bound.arguments)
                except TypeError:
                    # Signature didn't bind cleanly — fall through with raw kwargs.
                    pass
            if is_template:
                try:
                    rendered = name.format(**full_args)
                except (KeyError, IndexError):
                    rendered = name
            else:
                rendered = name
            with recorder.record_call(kind=kind, name=rendered, args=full_args):
                return await handler(*args, **kwargs)

        return wrapper  # pyright: ignore[reportReturnType]

    return decorator


def summarize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Render kwargs for logging without leaking large payloads or secrets.

    - Long strings: truncated with marker.
    - Lists: replaced with "<list: N items>" so we don't dump 100 ops verbatim.
    - Pydantic models / dataclasses: replaced with their type name so we
      don't accidentally serialize an entire Operation tree per tool call.
    - Everything else: passes through.
    """
    out: dict[str, Any] = {}
    for key, value in args.items():
        out[key] = _summarize_value(value)
    return out


def _summarize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _truncate(value, limit=200)
    if isinstance(value, list):
        items = cast("list[Any]", value)
        return f"<list: {len(items)} item(s)>"
    if isinstance(value, BaseModel):
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        # Recurse one level so nested user data is also sanitized.
        nested = cast("dict[Any, Any]", value)
        return {k: _summarize_value(v) for k, v in nested.items()}
    return value


def _truncate(s: str, *, limit: int = 500) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"...[truncated, {len(s)} total]"


def _to_ms(elapsed: timedelta) -> int:
    return int(elapsed.total_seconds() * 1000)
