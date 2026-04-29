"""Time abstraction for testability.

Anything time-sensitive (TTL checks, audit log timestamps) takes a `Clock`
dependency rather than calling `datetime.now()` directly. Tests inject a
`FixedClock` or similar; production wires in `SystemClock`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Source of truth for the current instant in time."""

    def now(self) -> datetime: ...


class SystemClock:
    """Production clock: wraps `datetime.now(UTC)`."""

    def now(self) -> datetime:
        return datetime.now(UTC)
