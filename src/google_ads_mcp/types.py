"""Internal types — the lingua franca above the SDK boundary.

Anything in this module is safe to import from any layer. It must not import
`google.ads.googleads.*` (or any other vendor type) so the rest of the codebase
can be tested without the SDK installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Google Ads customer IDs are 10-digit numeric strings (no dashes). We keep
# this as a plain `str` alias for ergonomics; validation lives at the boundary
# where strings cross from MCP input into the SDK.
CustomerId = str


@dataclass(frozen=True, slots=True)
class GaqlRow:
    """A single GAQL result row, flat-keyed by the field path used in SELECT.

    The SDK returns rows as nested protos (`row.campaign.id`, `row.metrics.cost_micros`).
    We flatten to dotted keys (`{"campaign.id": ..., "metrics.cost_micros": ...}`)
    so the LLM sees the same notation it wrote in the query.
    """

    fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GaqlResult:
    """Outcome of a GAQL query.

    `truncated` indicates that we stopped iterating before exhausting the
    server's result set (row cap or byte budget hit). `truncation_reason` is
    a short human-readable explanation suitable for showing to the LLM so it
    can decide whether to refine the query or page through.
    """

    rows: list[GaqlRow]
    total_rows_returned: int
    truncated: bool
    truncation_reason: str | None = None
