"""Internal types — the lingua franca above the SDK boundary.

Anything in this module is safe to import from any layer. It must not import
`google.ads.googleads.*` (or any other vendor type) so the rest of the codebase
can be tested without the SDK installed.

Pydantic models give us free JSON-schema generation at the MCP boundary.
Where validation is meaningless (string aliases, raw row dicts), we stay
type-only.
"""

from __future__ import annotations

from typing import Any, Literal

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


# === Mutate path types ======================================================

OperationKind = Literal["create", "update", "remove"]


class Operation(BaseModel):
    """One operation within a generic mutate call.

    `service` is the snake_case Google Ads service name (e.g. "campaign",
    "campaign_budget", "ad_group_criterion"). `resource` carries the resource
    fields — for create/update, the entity payload; for remove, just
    `resource_name`. `update_mask` is required for updates (the SDK rejects
    field changes that aren't masked) and ignored for create/remove.

    `force_override=True` bypasses threshold guardrails (CPC/budget caps)
    for this single operation. Batch-size and customer-allowlist guardrails
    are not overridable.
    """

    model_config = ConfigDict(frozen=True)

    service: str = Field(
        description=(
            "Snake_case service name, e.g. 'campaign'. Use the gads-schema:// "
            "resource to discover field names per service."
        ),
    )
    op: OperationKind
    resource: dict[str, Any] = Field(
        description=(
            "Resource fields. For create: full payload. For update: the fields "
            "you're changing plus 'resource_name'. For remove: only 'resource_name'."
        ),
    )
    update_mask: list[str] | None = Field(
        default=None,
        description=(
            "Required for update ops; list of dotted field paths being changed. "
            "Omit for create/remove."
        ),
    )
    force_override: bool = Field(
        default=False,
        description=(
            "If true, threshold guardrails (CPC, budget) are bypassed for this "
            "operation. Audit log records the bypass. Cannot bypass batch-size "
            "or customer-allowlist guardrails."
        ),
    )


class OperationDiff(BaseModel):
    """Human-readable preview of a single operation."""

    model_config = ConfigDict(frozen=True)

    service: str
    op: OperationKind
    summary: str = Field(
        description="One-line summary, e.g. 'update campaign customers/.../campaigns/...'.",
    )
    detail: str = Field(
        description=(
            "Multi-line rendered detail safe to show to the LLM. For updates, "
            "lists masked field names and proposed values. For creates, the "
            "full payload. For removes, the resource being removed."
        ),
    )


class MutatePreview(BaseModel):
    """Result of a Layer-2 `mutate` call (validate_only=true phase).

    Returned to the LLM (or human) for review before they call `apply`.
    """

    model_config = ConfigDict(frozen=True)

    mutate_id: str = Field(
        description="Opaque token; pass to apply() to commit. Has a TTL.",
    )
    customer_id: CustomerId
    operations_count: int
    diffs: list[OperationDiff]
    expires_at_iso: str = Field(
        description="UTC ISO-8601 timestamp after which this mutate_id is unusable.",
    )


class ApplyResult(BaseModel):
    """Result of committing a previously previewed mutate."""

    model_config = ConfigDict(frozen=True)

    mutate_id: str
    customer_id: CustomerId
    applied: bool = Field(
        description=(
            "True for the first successful apply; False for idempotent re-apply "
            "of an already-committed mutate_id (the original result is returned)."
        ),
    )
    resource_names: list[str] = Field(
        description="Resource names returned by the API for each operation.",
    )
