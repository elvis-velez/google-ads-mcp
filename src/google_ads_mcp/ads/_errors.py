# pyright: basic
"""Vendor-error → internal `ApiError` translation.

The Google Ads SDK raises two distinct exception families:

- `GoogleAdsException` — Ads-service-level errors, with a structured
  `failure` proto carrying error code, location, and message.
- `google.api_core.exceptions.GoogleAPICallError` — transport-level gRPC
  errors (`PermissionDenied`, `NotFound`, `ResourceExhausted`, etc.) that
  surface for things like "Google Ads API not enabled on your Cloud project"
  or "quota exceeded".

Every SDK call inside `ads/` runs under `translate_errors` so callers
upstream see only `ApiError` with a clean message — never a leaked
gRPC stack trace.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from google.ads.googleads.errors import GoogleAdsException
from google.api_core.exceptions import GoogleAPICallError

from google_ads_mcp.errors import ApiError


@contextmanager
def translate_errors(op: str) -> Iterator[None]:
    """Map any vendor exception inside the block to `ApiError`.

    `op` is a short human-readable label included in the error message
    (e.g. "ListAccessibleCustomers").
    """
    try:
        yield
    except GoogleAdsException as e:
        raise ApiError(
            f"{op} failed: {format_google_ads_failure(e)}",
            request_id=getattr(e, "request_id", None),
        ) from e
    except GoogleAPICallError as e:
        # GoogleAPICallError.message is the human-readable payload Google sent
        # back; str(e) prepends the HTTP code and adds noise.
        raise ApiError(f"{op} failed: {e.message}") from e


def format_google_ads_failure(e: GoogleAdsException) -> str:
    """Render a GoogleAdsException's failure list compactly."""
    failure = getattr(e, "failure", None)
    if failure is None:
        return str(e)
    parts: list[str] = []
    for err in failure.errors:
        loc = ""
        location = getattr(err, "location", None)
        if location is not None and getattr(location, "field_path_elements", None):
            loc = ":" + ".".join(p.field_name for p in location.field_path_elements)
        parts.append(f"{_format_error_code(err.error_code)}{loc}: {err.message}")
    return "; ".join(parts) or str(e)


def _format_error_code(error_code: Any) -> str:
    """Render a GoogleAdsErrorCode oneof as 'field_name: ENUM_VALUE'.

    `error_code` is a oneof proto; `str()` on it emits the active field on
    its own line which produces ugly multi-line error messages. Extracting
    the active oneof field directly keeps the message on one line.
    """
    which = getattr(error_code, "WhichOneof", None)
    if not callable(which):
        return str(error_code).strip().replace("\n", " ")
    active = which("error_code")
    if not isinstance(active, str):
        return str(error_code).strip().replace("\n", " ")
    value = getattr(error_code, active, None)
    name = getattr(value, "name", None) or str(value)
    return f"{active}: {name}"
