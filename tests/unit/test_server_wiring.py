"""Smoke tests for build_server's DI seam.

These prove that `build_server` can be constructed with an injected mock
client — i.e., unit tests don't need real credentials. If this test ever
needs `credentials.yaml`, the SDK boundary leaked.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from google_ads_mcp.server import build_server
from google_ads_mcp.settings import Settings


def test_builds_with_injected_client() -> None:
    server = build_server(settings=Settings(), client=MagicMock())

    assert server.name == "google-ads-mcp"


def test_skips_credential_loading_when_client_given(tmp_path: object) -> None:
    # Point credentials_path at a non-existent file; build_server must NOT
    # try to read it because we're injecting a client directly.
    settings = Settings(credentials_path=tmp_path / "does-not-exist.yaml")  # type: ignore[operator]

    server = build_server(settings=settings, client=MagicMock())

    assert server.name == "google-ads-mcp"


def test_wires_audit_and_pending(tmp_path: object) -> None:
    """All Phase-2 dependencies are constructable from injected primitives;
    no real I/O happens at build time."""
    settings = Settings(
        credentials_path=tmp_path / "nope.yaml",  # type: ignore[operator]
        audit_log_path=tmp_path / "nope-audit.log",  # type: ignore[operator]
    )

    server = build_server(settings=settings, client=MagicMock())

    # No audit file written just by constructing — proves IO is deferred.
    assert not (tmp_path / "nope-audit.log").exists()  # type: ignore[operator]
    assert server.name == "google-ads-mcp"
