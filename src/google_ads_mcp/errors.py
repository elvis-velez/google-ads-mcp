"""Exception hierarchy.

All errors raised inside the server are subclasses of `AdsMcpError`. The
FastMCP layer maps them to MCP error responses with clean human-readable
messages — no leaked stack traces, no proto-internal noise.

Phase-2+ errors (GuardrailViolation, ValidationFailed, PendingNotFound,
PendingExpired) land when their throwers do.
"""

from __future__ import annotations


class AdsMcpError(Exception):
    """Base for all errors raised by this server."""


class ConfigError(AdsMcpError):
    """Configuration is missing, malformed, or invalid.

    Raised at startup or on first config-dependent call. Should fail fast
    with a message that tells the operator exactly what to fix.
    """


class AuthError(AdsMcpError):
    """Authentication or credential failure.

    Distinct from `ConfigError` — config is well-formed but the credentials
    it points at are rejected by Google (expired refresh token, revoked
    consent, wrong dev token).
    """


class ApiError(AdsMcpError):
    """A Google Ads API call failed.

    Wraps the SDK's `GoogleAdsException` so callers above the SDK boundary
    don't import vendor types. `request_id` (when available) is the API
    request ID Google returns for debugging — quote it when filing a Google
    Ads support ticket.
    """

    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id
