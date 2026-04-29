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

from google.ads.googleads.client import GoogleAdsClient

from google_ads_mcp.ads._errors import translate_errors
from google_ads_mcp.ads._proto import approximate_size, flatten
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

    with translate_errors(f"GAQL[customer={customer_id}]"):
        stream = service.search_stream(customer_id=customer_id, query=query)
        for batch in stream:
            paths = list(batch.field_mask.paths)
            for proto_row in batch.results:
                flat = flatten(proto_row, paths)
                row_size = approximate_size(flat)
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

    return GaqlResult(
        rows=rows,
        total_rows_returned=len(rows),
        truncated=truncated,
        truncation_reason=truncation_reason,
    )
