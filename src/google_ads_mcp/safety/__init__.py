"""Safety — enforcement, not recording.

Three pieces, all pure logic on internal types (no SDK imports, no I/O):

- `allowlist.py` — customer-ID allowlist + the matching guardrail check.
- `diff.py` — operation → human-readable preview rendered before commit.
- `pending.py` — TTL'd, idempotent mutate_id store backing the two-phase
  apply contract.

These are *server-side invariants*, not operator policy. Business rules
like "max CPC for this account" are deliberately not here — that's the
operator's call and lives in workflow above this server.

The audit log lives in `observability/`, not here, because it *records*
what enforcement decided rather than enforcing anything itself.
"""
