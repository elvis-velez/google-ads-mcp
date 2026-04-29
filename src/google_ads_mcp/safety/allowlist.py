"""Customer-ID allowlist.

Confused-deputy mitigation: every Layer-2 tool checks that the requested
`customer_id` is in the install's `ListAccessibleCustomers` response before
crossing the SDK boundary. Lazy-loaded on first use, then cached for the
server's lifetime — the accessible list rarely changes mid-session and a
fresh restart is the documented way to refresh.

The fetcher is injected so unit tests don't need an SDK client.
"""

from __future__ import annotations

import threading
from collections.abc import Callable


class CustomerAllowlist:
    """Lazy-loaded cache of accessible customer IDs."""

    def __init__(self, fetch: Callable[[], list[str]]) -> None:
        self._fetch = fetch
        self._cached: frozenset[str] | None = None
        self._lock = threading.Lock()

    def is_allowed(self, customer_id: str) -> bool:
        return customer_id in self._all()

    def all(self) -> list[str]:
        return sorted(self._all())

    def _all(self) -> frozenset[str]:
        with self._lock:
            if self._cached is None:
                self._cached = frozenset(self._fetch())
            return self._cached
