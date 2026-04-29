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


class GuardrailViolation(AdsMcpError):
    """A safety guardrail rejected an operation before it reached the API.

    Raised by `safety/guardrails.py` when an op exceeds CPC, budget, or
    batch-size caps. The message names the violated cap and the offending
    value so the LLM can either correct the request or pass `force_override`.
    """


class ValidationFailed(AdsMcpError):
    """Google Ads validate_only=true returned errors.

    Distinct from ApiError: the API call succeeded transport-wise, but the
    operation would be rejected if applied. Surface to the LLM so it can
    fix the operation rather than retry.
    """


class PendingNotFound(AdsMcpError):
    """An apply was attempted with a mutate_id we've never seen.

    Either the LLM fabricated the id or used one from a different server
    process (mutate_ids don't survive server restarts).
    """


class PendingExpired(AdsMcpError):
    """A mutate_id existed but its TTL elapsed before apply.

    Re-issue the mutate with the same operations to get a fresh id. The
    TTL exists to prevent stale previews getting applied long after the
    underlying account state may have changed.
    """
