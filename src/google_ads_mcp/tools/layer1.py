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
from google_ads_mcp.observability.activity import ActivityRecorder, with_activity
from google_ads_mcp.observability.audit import AuditLogger
from google_ads_mcp.safety.allowlist import CustomerAllowlist, check_customer_allowlist
from google_ads_mcp.safety.pending import PendingStore
from google_ads_mcp.settings import Settings
from google_ads_mcp.tools._flow import perform_mutate
from google_ads_mcp.types import GaqlResult, MutatePreview, Operation

_USD_TO_MICROS = 1_000_000


def _status_op(service: str, resource_name: str, status: str) -> Operation:
    """Build an UPDATE operation that sets a resource's status field.

    Six pause/enable tools share this exact shape — a tiny helper here keeps
    each tool ~3 lines and removes a class of typo-induced bugs (wrong
    update_mask, mismatched status enum, etc.).
    """
    return Operation(
        service=service,
        op="update",
        resource={"resource_name": resource_name, "status": status},
        update_mask=["status"],
    )

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
    audit: AuditLogger,
    activity: ActivityRecorder,
) -> None:
    """Register Layer 1 outcome tools."""

    async def _preview(customer_id: str, op: Operation) -> MutatePreview:
        return await asyncio.to_thread(
            perform_mutate,
            client=client,
            customer_id=customer_id,
            operations=[op],
            allowlist=allowlist,
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
    @with_activity(activity, name="pause_campaign")
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
        return await _preview(
            customer_id,
            _status_op(
                "campaign",
                f"customers/{customer_id}/campaigns/{campaign_id}",
                "PAUSED",
            ),
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Enable a campaign (preview)",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="enable_campaign")
    async def enable_campaign(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[
            str, Field(description="10-digit Google Ads customer ID, no dashes.")
        ],
        campaign_id: Annotated[str, Field(description="Numeric campaign ID.")],
    ) -> MutatePreview:
        """Preview enabling (un-pausing) a campaign. Call apply() to commit."""
        return await _preview(
            customer_id,
            _status_op(
                "campaign",
                f"customers/{customer_id}/campaigns/{campaign_id}",
                "ENABLED",
            ),
        )

    # ---------------- ad group status ---------------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Pause an ad group (preview)",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="pause_ad_group")
    async def pause_ad_group(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[str, Field(description="10-digit customer ID.")],
        ad_group_id: Annotated[
            str,
            Field(
                description=(
                    "Numeric ad group ID (trailing digits of ad_group.resource_name)."
                ),
            ),
        ],
    ) -> MutatePreview:
        """Preview pausing an ad group. Use for granular tactical pauses without
        touching the parent campaign's status."""
        return await _preview(
            customer_id,
            _status_op(
                "ad_group",
                f"customers/{customer_id}/adGroups/{ad_group_id}",
                "PAUSED",
            ),
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Enable an ad group (preview)",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="enable_ad_group")
    async def enable_ad_group(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[str, Field(description="10-digit customer ID.")],
        ad_group_id: Annotated[str, Field(description="Numeric ad group ID.")],
    ) -> MutatePreview:
        """Preview enabling (un-pausing) an ad group."""
        return await _preview(
            customer_id,
            _status_op(
                "ad_group",
                f"customers/{customer_id}/adGroups/{ad_group_id}",
                "ENABLED",
            ),
        )

    # ---------------- keyword status + bid ----------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Pause a keyword (preview)",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="pause_keyword")
    async def pause_keyword(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[str, Field(description="10-digit customer ID.")],
        criterion_resource_name: Annotated[
            str,
            Field(
                description=(
                    "Full ad_group_criterion resource name, e.g. "
                    "'customers/1234567890/adGroupCriteria/111~222'. The trailing "
                    "id has the form '{ad_group_id}~{criterion_id}'; pass the "
                    "whole string from your GAQL result."
                ),
            ),
        ],
    ) -> MutatePreview:
        """Preview pausing a keyword (ad-group criterion). Most common tactical
        optimization — kill an underperformer without touching its ad group."""
        return await _preview(
            customer_id,
            _status_op("ad_group_criterion", criterion_resource_name, "PAUSED"),
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Enable a keyword (preview)",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="enable_keyword")
    async def enable_keyword(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[str, Field(description="10-digit customer ID.")],
        criterion_resource_name: Annotated[
            str,
            Field(description="Full ad_group_criterion resource name."),
        ],
    ) -> MutatePreview:
        """Preview enabling (un-pausing) a keyword."""
        return await _preview(
            customer_id,
            _status_op("ad_group_criterion", criterion_resource_name, "ENABLED"),
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Set keyword CPC bid (preview)",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="set_keyword_bid")
    async def set_keyword_bid(  # pyright: ignore[reportUnusedFunction]
        customer_id: Annotated[str, Field(description="10-digit customer ID.")],
        criterion_resource_name: Annotated[
            str,
            Field(description="Full ad_group_criterion resource name."),
        ],
        cpc_usd: Annotated[
            float,
            Field(
                gt=0,
                description="New max CPC bid in USD. Converted to micros internally.",
            ),
        ],
    ) -> MutatePreview:
        """Preview a keyword CPC bid change. USD → micros internally.

        The new bid will be visible in the diff before commit; use that
        as the safety check rather than relying on a server-side cap.
        """
        op = Operation(
            service="ad_group_criterion",
            op="update",
            resource={
                "resource_name": criterion_resource_name,
                "cpc_bid_micros": round(cpc_usd * _USD_TO_MICROS),
            },
            update_mask=["cpc_bid_micros"],
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
    @with_activity(activity, name="set_campaign_budget")
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
    @with_activity(activity, name="add_negative_keyword")
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
    @with_activity(activity, name="account_summary")
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
            check_customer_allowlist(customer_id, allowlist=allowlist)
            return gaql_impl.search(
                client,
                customer_id,
                query,
                max_rows=settings.gaql_max_rows,
                max_bytes=settings.gaql_max_response_bytes,
            )

        return await asyncio.to_thread(go)
