"""Layer 1 — outcome-shaped tools.

Workflow wrappers around Layer-2 mutate. Each tool constructs a single
`Operation` with the right service / op / resource and runs the shared
`perform_mutate` flow — guardrails, validate-only, diff, pending. The LLM
then calls `apply(mutate_id)` to commit. No auto-apply path in v1; every
write requires an explicit apply step (per the safety model).

`account_summary` is the read-side outcome: a pre-baked GAQL query for the
common "show me how each campaign is doing" question.

Adding a new Layer-1 tool is a single function — the safety machinery and
SDK translation already handle every standard service.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from google_ads_mcp.ads import gaql as gaql_impl
from google_ads_mcp.observability.audit import AuditLogger
from google_ads_mcp.safety import guardrails
from google_ads_mcp.safety.allowlist import CustomerAllowlist
from google_ads_mcp.safety.limits import LimitsConfig
from google_ads_mcp.safety.pending import PendingStore
from google_ads_mcp.settings import Settings
from google_ads_mcp.tools._flow import perform_mutate
from google_ads_mcp.types import GaqlResult, MutatePreview, Operation

_USD_TO_MICROS = 1_000_000

DateRange = Literal[
    "TODAY",
    "YESTERDAY",
    "LAST_7_DAYS",
    "LAST_14_DAYS",
    "LAST_30_DAYS",
    "THIS_MONTH",
    "LAST_MONTH",
    "ALL_TIME",
]
NegativeScope = Literal["campaign", "ad_group"]
MatchType = Literal["BROAD", "PHRASE", "EXACT"]


def register_layer1(
    mcp: FastMCP,
    *,
    client: Any,
    settings: Settings,
    pending: PendingStore,
    allowlist: CustomerAllowlist,
    limits: LimitsConfig,
    audit: AuditLogger,
) -> None:
    """Register Layer 1 outcome tools."""

    async def _preview(customer_id: str, op: Operation) -> MutatePreview:
        return await asyncio.to_thread(
            perform_mutate,
            client=client,
            customer_id=customer_id,
            operations=[op],
            settings=settings,
            allowlist=allowlist,
            limits=limits,
            pending=pending,
            audit=audit,
        )

    # ---------------- campaign status ----------------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Pause a campaign (preview)",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    async def pause_campaign(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[
            str, Field(description="10-digit Google Ads customer ID, no dashes.")
        ],
        campaign_id: Annotated[
            str,
            Field(
                description=(
                    "Numeric campaign ID (the trailing digits of campaign.resource_name)."
                ),
            ),
        ],
    ) -> MutatePreview:
        """Preview pausing a campaign. Returns a mutate_id; call apply() to commit."""
        op = Operation(
            service="campaign",
            op="update",
            resource={
                "resource_name": f"customers/{customer_id}/campaigns/{campaign_id}",
                "status": "PAUSED",
            },
            update_mask=["status"],
        )
        return await _preview(customer_id, op)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Enable a campaign (preview)",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    async def enable_campaign(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[
            str, Field(description="10-digit Google Ads customer ID, no dashes.")
        ],
        campaign_id: Annotated[str, Field(description="Numeric campaign ID.")],
    ) -> MutatePreview:
        """Preview enabling (un-pausing) a campaign. Call apply() to commit."""
        op = Operation(
            service="campaign",
            op="update",
            resource={
                "resource_name": f"customers/{customer_id}/campaigns/{campaign_id}",
                "status": "ENABLED",
            },
            update_mask=["status"],
        )
        return await _preview(customer_id, op)

    # ---------------- budget ------------------------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Set campaign budget (preview)",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    async def set_campaign_budget(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[str, Field(description="10-digit customer ID.")],
        budget_id: Annotated[
            str,
            Field(
                description=(
                    "Numeric campaign-budget ID (the trailing digits of "
                    "campaign_budget.resource_name). Find via GAQL on the "
                    "campaign_budget resource."
                ),
            ),
        ],
        daily_amount_usd: Annotated[
            float,
            Field(
                gt=0,
                description="New daily budget in USD. Converted to micros internally.",
            ),
        ],
    ) -> MutatePreview:
        """Preview a daily budget change. USD is converted to micros internally."""
        op = Operation(
            service="campaign_budget",
            op="update",
            resource={
                "resource_name": f"customers/{customer_id}/campaignBudgets/{budget_id}",
                "amount_micros": round(daily_amount_usd * _USD_TO_MICROS),
            },
            update_mask=["amount_micros"],
        )
        return await _preview(customer_id, op)

    # ---------------- negative keywords -------------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Add a negative keyword (preview)",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    async def add_negative_keyword(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[str, Field(description="10-digit customer ID.")],
        scope: Annotated[
            NegativeScope,
            Field(
                description=(
                    "'campaign' for a campaign-level negative (CampaignCriterion); "
                    "'ad_group' for an ad-group-level negative (AdGroupCriterion)."
                ),
            ),
        ],
        ref_id: Annotated[
            str,
            Field(
                description=(
                    "Numeric ID of the campaign (when scope='campaign') or ad group "
                    "(when scope='ad_group') to attach the negative to."
                ),
            ),
        ],
        text: Annotated[str, Field(description="Negative keyword text.")],
        match_type: Annotated[
            MatchType,
            Field(
                description="Match type. EXACT is most common for negatives.",
            ),
        ] = "EXACT",
    ) -> MutatePreview:
        """Preview adding a negative keyword. Call apply() to commit."""
        if scope == "campaign":
            op = Operation(
                service="campaign_criterion",
                op="create",
                resource={
                    "campaign": f"customers/{customer_id}/campaigns/{ref_id}",
                    "negative": True,
                    "keyword": {"text": text, "match_type": match_type},
                },
            )
        else:
            op = Operation(
                service="ad_group_criterion",
                op="create",
                resource={
                    "ad_group": f"customers/{customer_id}/adGroups/{ref_id}",
                    "negative": True,
                    "keyword": {"text": text, "match_type": match_type},
                },
            )
        return await _preview(customer_id, op)

    # ---------------- account summary (read) --------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Account performance summary",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    async def account_summary(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[str, Field(description="10-digit customer ID.")],
        date_range: Annotated[
            DateRange,
            Field(
                description=(
                    "GAQL date-range literal. LAST_7_DAYS is the canonical 'how are "
                    "we doing' window."
                ),
            ),
        ] = "LAST_7_DAYS",
    ) -> GaqlResult:
        """Per-campaign performance for the date range, sorted by spend descending.

        Pre-baked GAQL: campaign id/name/status plus impressions, clicks, cost,
        conversions, and cost-per-conversion. Use the gaql tool directly for
        anything more complex.
        """
        query = (
            "SELECT campaign.id, campaign.name, campaign.status, "
            "metrics.impressions, metrics.clicks, metrics.cost_micros, "
            "metrics.conversions, metrics.cost_per_conversion "
            f"FROM campaign WHERE segments.date DURING {date_range} "
            "ORDER BY metrics.cost_micros DESC"
        )

        def go() -> GaqlResult:
            guardrails.check_customer_allowlist(customer_id, allowlist=allowlist)
            return gaql_impl.search(
                client,
                customer_id,
                query,
                max_rows=settings.gaql_max_rows,
                max_bytes=settings.gaql_max_response_bytes,
            )

        return await asyncio.to_thread(go)
