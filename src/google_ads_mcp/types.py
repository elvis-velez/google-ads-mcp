"""Internal types — the lingua franca above the SDK boundary.

Anything in this module is safe to import from any layer. It must not import
`google.ads.googleads.*` (or any other vendor type) so the rest of the codebase
can be tested without the SDK installed.

Pydantic models give us free JSON-schema generation at the MCP boundary.
Where validation is meaningless (string aliases, raw row dicts), we stay
type-only.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Google Ads customer IDs are 10-digit numeric strings (no dashes). We keep
# this as a plain `str` alias for ergonomics; validation lives at the boundary
# where strings cross from MCP input into the SDK.
CustomerId = str

# A single GAQL result row: flat dict keyed by the dotted field path used in
# SELECT. e.g. {"campaign.id": 42, "campaign.name": "Brand US"}.
# Kept as `dict[str, Any]` rather than a wrapper model so JSON output to the
# LLM doesn't gain a useless `{"fields": {...}}` envelope around each row.
GaqlRow = dict[str, Any]


class GaqlResult(BaseModel):
    """Outcome of a GAQL query.

    Rows arrive flat-keyed using the dotted paths from the SELECT clause.
    `truncated` indicates that we stopped before exhausting the server's
    result set (row cap or byte budget hit); `truncation_reason` tells the
    caller why so it can decide whether to refine, narrow, or page with
    LIMIT/OFFSET.
    """

    model_config = ConfigDict(frozen=True)

    rows: list[GaqlRow] = Field(
        description=(
            "List of result rows; each row is a flat object keyed by the dotted "
            "field path used in the GAQL SELECT clause."
        ),
    )
    total_rows_returned: int
    truncated: bool
    truncation_reason: str | None = None


class ResourceFields(BaseModel):
    """Field metadata for a single Google Ads resource type.

    Returned by the `gads-schema://{resource_type}` MCP resource. Fields are
    partitioned by use: SELECT-able, WHERE-able, and ORDER BY-able. The same
    name often appears in multiple lists.
    """

    model_config = ConfigDict(frozen=True)

    resource_type: str
    selectable: list[str]
    filterable: list[str]
    sortable: list[str]


class AccessibleAccounts(BaseModel):
    """Customer IDs the current credentials can operate on.

    Returned by the `gads-account://accessible` MCP resource. IDs are
    10-digit strings, no dashes, sorted lexicographically.
    """

    model_config = ConfigDict(frozen=True)

    customer_ids: list[CustomerId]
