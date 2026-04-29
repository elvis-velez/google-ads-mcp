"""Cross-cutting safety: audit log, guardrails, diff renderer, pending store.

These modules are pure logic on internal types. They never import the SDK
(`# pyright: basic` lives in `ads/`, not here) and have no IO except the
audit logger's append-to-file. That's intentional — the unit-test bar is
"runs without credentials, runs without network."
"""
