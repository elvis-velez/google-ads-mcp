"""Observability — what happened.

Three sinks, each with a distinct audience:

- `audit.py`      — every state-changing attempt and its outcome. Compliance.
- `activity.py`   — every tool/resource call. Debugging.
- `diagnostics.py`— server lifecycle + operator-facing warnings. Stderr.

Distinct from `safety/`, which *enforces* policy. Observability *records*
what enforcement decided.
"""
