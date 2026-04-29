"""Safety — enforcement, not recording.

Guardrails, diff renderer, pending store, customer-allowlist, per-account
limits. Pure logic on internal types: no SDK imports, no I/O.

The audit log lives in `observability/`, not here, because it *records*
what enforcement decided rather than enforcing anything itself.
"""
