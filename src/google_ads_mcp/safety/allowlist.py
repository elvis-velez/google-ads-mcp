"""Customer-ID allowlist + guardrail check.

Confused-deputy mitigation: every Layer-2 tool checks that the requested
`customer_id` is in the install's `ListAccessibleCustomers` response before
crossing the SDK boundary. This is the only server-side enforcement that
actually represents an invariant rather than operator policy — if the LLM
hallucinates or constructs a `customer_id` from user input, the credentials
physically can't reach it and we should refuse cleanly.

The fetcher is injected so unit tests don't need an SDK client.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from google_ads_mcp.errors import GuardrailViolation


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


def check_customer_allowlist(
    customer_id: str, *, allowlist: CustomerAllowlist
) -> None:
    """Reject operations on customer_ids the install can't access."""
    if not allowlist.is_allowed(customer_id):
        raise GuardrailViolation(
            f"customer_id '{customer_id}' is not accessible by these credentials. "
            "Use the gads-account://accessible resource to list valid IDs."
        )
