"""TTL'd, idempotent pending-mutate store.

`store()` records a previewed mutate and returns a `mutate_id`. `apply()`
runs an injected applier function exactly once per id and caches the
result so re-applying the same id returns the cached `ApplyResult`
without re-mutating — that's the structural guarantee for safe retries.

Lock-protected with `threading.Lock` because callers may run inside
`asyncio.to_thread`, where multiple SDK calls share the same store via
different threads. The applier runs under the lock for v1; this serialises
concurrent applies but keeps reasoning simple. Layer-1 reads aren't
gated by this lock so they stay non-blocking.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from google_ads_mcp.errors import PendingExpired, PendingNotFound
from google_ads_mcp.safety.clock import Clock
from google_ads_mcp.types import ApplyResult, CustomerId, Operation


@dataclass
class _Entry:
    customer_id: CustomerId
    operations: list[Operation]
    expires_at: datetime
    applied_result: ApplyResult | None = field(default=None)


# applier(customer_id, operations) -> ApplyResult (modulo missing mutate_id);
# the store fills in the mutate_id on its way out.
Applier = Callable[[CustomerId, list[Operation]], ApplyResult]


class PendingStore:
    """In-memory store of validate_only-previewed mutates."""

    def __init__(
        self,
        *,
        clock: Clock,
        ttl: timedelta,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self._clock = clock
        self._ttl = ttl
        self._id_factory = id_factory

    def store(
        self,
        *,
        customer_id: CustomerId,
        operations: list[Operation],
    ) -> tuple[str, datetime]:
        """Persist a previewed mutate; return (mutate_id, expires_at)."""
        with self._lock:
            mutate_id = self._id_factory()
            expires_at = self._clock.now() + self._ttl
            self._entries[mutate_id] = _Entry(
                customer_id=customer_id,
                operations=list(operations),
                expires_at=expires_at,
            )
            return mutate_id, expires_at

    def apply(self, mutate_id: str, applier: Applier) -> ApplyResult:
        """Run `applier` exactly once per mutate_id; cache and return result.

        Raises `PendingNotFound` for unknown ids and `PendingExpired` for
        expired ids (which are removed from the store on access). For an
        already-applied id, returns the cached result without invoking
        `applier` again — the idempotency guarantee.
        """
        with self._lock:
            entry = self._entries.get(mutate_id)
            if entry is None:
                raise PendingNotFound(
                    f"mutate_id '{mutate_id}' not found. Re-issue the mutate "
                    "to get a fresh id (mutate_ids don't survive server restarts)."
                )
            if self._clock.now() > entry.expires_at:
                del self._entries[mutate_id]
                raise PendingExpired(
                    f"mutate_id '{mutate_id}' expired at {entry.expires_at.isoformat()}. "
                    "Re-issue the mutate to get a fresh id."
                )
            if entry.applied_result is not None:
                # Idempotent re-apply: return the cached result with applied=False
                # so callers can tell it didn't re-mutate. resource_names match
                # the original commit.
                return entry.applied_result.model_copy(update={"applied": False})

            result = applier(entry.customer_id, entry.operations)
            entry.applied_result = result
            return result

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
