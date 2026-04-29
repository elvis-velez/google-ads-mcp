# pyright: basic
"""GAQL execution.

Wraps `GoogleAdsService.search_stream` into a synchronous, capped query runner
that returns internal `GaqlResult` objects. Two caps protect the LLM context
window: `max_rows` and `max_bytes`. The first cap to fire wins.

Pagination beyond the cap is the caller's job — they get a `truncated=True`
result with a reason string and can re-issue with `LIMIT`/`OFFSET` or a
tighter `SELECT`.
"""

from __future__ import annotations

import enum
from typing import Any

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

from google_ads_mcp.errors import ApiError
from google_ads_mcp.types import CustomerId, GaqlResult, GaqlRow  # GaqlRow is dict[str, Any]


def search(
    client: GoogleAdsClient,
    customer_id: CustomerId,
    query: str,
    *,
    max_rows: int,
    max_bytes: int,
) -> GaqlResult:
    """Run a GAQL SELECT and collect rows up to the given caps."""
    service = client.get_service("GoogleAdsService")

    rows: list[GaqlRow] = []
    bytes_used = 0
    truncated = False
    truncation_reason: str | None = None

    try:
        stream = service.search_stream(customer_id=customer_id, query=query)
        for batch in stream:
            paths = list(batch.field_mask.paths)
            for proto_row in batch.results:
                flat = _flatten(proto_row, paths)
                row_size = _approximate_size(flat)
                if bytes_used + row_size > max_bytes:
                    truncated = True
                    truncation_reason = (
                        f"response byte budget reached ({max_bytes:,} bytes); "
                        "tighten SELECT or page with LIMIT/OFFSET"
                    )
                    break
                rows.append(flat)
                bytes_used += row_size
                if len(rows) >= max_rows:
                    truncated = True
                    truncation_reason = (
                        f"max_rows={max_rows} reached; "
                        "page with LIMIT/OFFSET or narrow the WHERE clause"
                    )
                    break
            if truncated:
                break
    except GoogleAdsException as e:
        raise ApiError(
            _format_failure(e),
            request_id=getattr(e, "request_id", None),
        ) from e

    return GaqlResult(
        rows=rows,
        total_rows_returned=len(rows),
        truncated=truncated,
        truncation_reason=truncation_reason,
    )


def _flatten(proto_row: Any, paths: list[str]) -> dict[str, Any]:
    """Walk each dotted field path into `proto_row`, building a flat dict."""
    out: dict[str, Any] = {}
    for path in paths:
        value: Any = proto_row
        for part in path.split("."):
            value = getattr(value, part, None)
            if value is None:
                break
        out[path] = _coerce(value)
    return out


def _coerce(value: Any) -> Any:
    """Normalise SDK values for JSON-friendly downstream consumption.

    proto-plus enums are IntEnum-like; we report `.name` so the LLM sees
    `"ENABLED"` not `2`. Everything else is already a Python primitive.
    """
    if value is None:
        return None
    if isinstance(value, enum.Enum):
        return value.name
    return value


def _approximate_size(row: dict[str, Any]) -> int:
    """Rough byte cost of a row when rendered. Used to enforce `max_bytes`."""
    return sum(len(k) + len(repr(v)) for k, v in row.items())


def _format_failure(e: GoogleAdsException) -> str:
    """Render a GoogleAdsException's failure list compactly for error messages."""
    failure = getattr(e, "failure", None)
    if failure is None:
        return str(e)
    parts: list[str] = []
    for err in failure.errors:
        loc = ""
        location = getattr(err, "location", None)
        if location is not None and getattr(location, "field_path_elements", None):
            loc = (
                ":"
                + ".".join(p.field_name for p in location.field_path_elements)
            )
        parts.append(f"{err.error_code}{loc}: {err.message}")
    return "; ".join(parts) or str(e)
