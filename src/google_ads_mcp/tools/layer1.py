"""Layer 1 — outcome-shaped tools.

Workflow wrappers around Layer-2 mutate. Each tool binds a common LLM
intent ("pause this keyword", "set this bid") to an `Operation` with the
right service/op/resource shape, then runs the shared `perform_mutate`
flow — allowlist check, validate-only, diff, pending. The LLM then calls
`apply(mutate_id)` to commit.

Layer-1 tools exist for the operations that benefit from a tight typed
schema (USD-to-micros conversion, Literal enums, baked-in resource_name
paths). The long tail of Google Ads operations goes through the generic
Layer-2 `mutate` escape hatch.

The one exception is `apply_recommendation`, which uses a different SDK
endpoint (RecommendationService, not the GoogleAdsService.Mutate oneof)
and therefore cannot route through `perform_mutate`. It's the only Layer-1
tool that's a one-shot rather than two-phase.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from google_ads_mcp.ads import _proto
from google_ads_mcp.ads import recommendations as recommendations_impl
from google_ads_mcp.ads import rpc as rpc_impl
from google_ads_mcp.errors import ValidationFailed
from google_ads_mcp.observability.activity import ActivityRecorder, with_activity
from google_ads_mcp.observability.audit import AuditEvent, AuditLogger
from google_ads_mcp.safety.allowlist import CustomerAllowlist, check_customer_allowlist
from google_ads_mcp.safety.pending import PendingStore
from google_ads_mcp.tools._flow import perform_mutate
from google_ads_mcp.types import ApplyResult, MutatePreview, Operation, RpcCall

_USD_TO_MICROS = 1_000_000

# Surfaced in every tool's input schema; rejects malformed IDs (dashes,
# wrong length) before they hit the allowlist or SDK.
CustomerIdArg = Annotated[
    str,
    Field(
        pattern=r"^\d{10}$",
        description="10-digit Google Ads customer ID, no dashes.",
    ),
]


def _operation_to_dict(op: Operation) -> dict[str, Any]:
    """Internal `Operation` → a `MutateOperation`-shaped dict.

    The batch_job's `add_operations` step accepts a list of MutateOperation
    protos. We build them as plain dicts here so the generic RPC dispatcher
    can marshal them via `setattr` — the proto-plus client accepts nested
    dicts as message values.
    """
    if op.op == "remove":
        return {f"{op.service}_operation": {"remove": op.resource.get("resource_name", "")}}
    if op.op == "create":
        return {f"{op.service}_operation": {"create": op.resource}}
    # update — needs update_mask
    return {
        f"{op.service}_operation": {
            "update": op.resource,
            "update_mask": {"paths": list(op.update_mask or [])},
        }
    }


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

NegativeScope = Literal["campaign", "ad_group"]
MatchType = Literal["BROAD", "PHRASE", "EXACT"]


def register_layer1(
    mcp: FastMCP,
    *,
    client: Any,
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
            title="Preview pause campaign",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="pause_campaign")
    async def pause_campaign(  # pyright: ignore[reportUnusedFunction]
        customer_id: CustomerIdArg,
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
            title="Preview enable campaign",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="enable_campaign")
    async def enable_campaign(  # pyright: ignore[reportUnusedFunction]
        customer_id: CustomerIdArg,
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
            title="Preview pause ad group",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="pause_ad_group")
    async def pause_ad_group(  # pyright: ignore[reportUnusedFunction]
        customer_id: CustomerIdArg,
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
            title="Preview enable ad group",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="enable_ad_group")
    async def enable_ad_group(  # pyright: ignore[reportUnusedFunction]
        customer_id: CustomerIdArg,
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
            title="Preview pause keyword",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="pause_keyword")
    async def pause_keyword(  # pyright: ignore[reportUnusedFunction]
        customer_id: CustomerIdArg,
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
            title="Preview enable keyword",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="enable_keyword")
    async def enable_keyword(  # pyright: ignore[reportUnusedFunction]
        customer_id: CustomerIdArg,
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
            title="Preview set keyword CPC bid",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="set_keyword_bid")
    async def set_keyword_bid(  # pyright: ignore[reportUnusedFunction]
        customer_id: CustomerIdArg,
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

        Sets `cpc_bid_micros` — the cash CPC bid. Campaigns using
        percent-CPC bidding strategies use a different field
        (`percent_cpc_bid_micros`) and will reject this update; route
        those through the Layer-2 `mutate` tool with the right field name.

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
            title="Preview set daily campaign budget",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="set_campaign_budget")
    async def set_campaign_budget(  # pyright: ignore[reportUnusedFunction]
        customer_id: CustomerIdArg,
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
        """Preview a daily budget change. USD is converted to micros internally.

        Sets `amount_micros`, the daily-spend field. Custom-period
        budgets (rare) use `total_amount_micros` instead — route those
        through the Layer-2 `mutate` tool with the right field name.
        """
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
            title="Preview add negative keyword",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="add_negative_keyword")
    async def add_negative_keyword(  # pyright: ignore[reportUnusedFunction]
        customer_id: CustomerIdArg,
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

    # ---------------- recommendations ---------------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Apply Google Ads recommendation",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="apply_recommendation")
    async def apply_recommendation(  # pyright: ignore[reportUnusedFunction]
        customer_id: CustomerIdArg,
        recommendation_resource_name: Annotated[
            str,
            Field(
                description=(
                    "Full recommendation resource name, e.g. "
                    "'customers/1234567890/recommendations/...'. Discover via GAQL "
                    "on the `recommendation` resource."
                ),
            ),
        ],
    ) -> ApplyResult:
        """Apply one Google Ads recommendation.

        One-shot: Google validates the recommendation when surfacing it, so
        there's no validate-only preview phase. The applied change is audited;
        the resulting entity (e.g. a new keyword from a keyword recommendation)
        shows up in Google Ads change history rather than this server's audit.
        """

        def go() -> ApplyResult:
            check_customer_allowlist(customer_id, allowlist=allowlist)
            try:
                resource_names = recommendations_impl.apply_recommendation(
                    client, customer_id, recommendation_resource_name
                )
            except Exception as e:
                audit.record(
                    AuditEvent(
                        phase="apply",
                        outcome="api_error",
                        mutate_id=None,
                        customer_id=customer_id,
                        payload_kind="rpc_call",
                        operations=None,
                        rpc_call=RpcCall(
                            service="recommendation_service",
                            method="apply_recommendation",
                            params={"resource_name": recommendation_resource_name},
                        ),
                        resource_names=None,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        error_request_id=getattr(e, "request_id", None),
                    )
                )
                raise

            audit.record(
                AuditEvent(
                    phase="apply",
                    outcome="ok",
                    mutate_id=None,
                    customer_id=customer_id,
                    payload_kind="rpc_call",
                    operations=None,
                    rpc_call=RpcCall(
                        service="recommendation_service",
                        method="apply_recommendation",
                        params={"resource_name": recommendation_resource_name},
                    ),
                    resource_names=resource_names,
                    error_type=None,
                    error_message=None,
                    error_request_id=None,
                )
            )
            return ApplyResult(
                mutate_id="",  # recommendations don't go through the pending store
                customer_id=customer_id,
                applied=True,
                resource_names=resource_names,
            )

        return await asyncio.to_thread(go)

    # ---------------- keyword ideas (read RPC) ------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Generate keyword ideas",
            readOnlyHint=True,
            destructiveHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="generate_keyword_ideas")
    async def generate_keyword_ideas(  # pyright: ignore[reportUnusedFunction]
        customer_id: CustomerIdArg,
        seed_type: Annotated[
            Literal["keyword", "url", "site", "keyword_and_url"],
            Field(
                description=(
                    "Which seed shape to use. 'keyword' takes a list of seed "
                    "phrases; 'url' takes a single landing page; 'site' takes a "
                    "domain name; 'keyword_and_url' combines both signals."
                ),
            ),
        ],
        keywords: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Seed keyword phrases. Required when seed_type is 'keyword' "
                    "or 'keyword_and_url'."
                ),
            ),
        ] = None,
        url: Annotated[
            str | None,
            Field(
                description=(
                    "Seed URL (a landing page). Required when seed_type is 'url' "
                    "or 'keyword_and_url'."
                ),
            ),
        ] = None,
        site: Annotated[
            str | None,
            Field(
                description=(
                    "Seed site domain (e.g. 'example.com'). Required when "
                    "seed_type is 'site'."
                ),
            ),
        ] = None,
        language: Annotated[
            str | None,
            Field(
                description=(
                    "Language constant resource name (e.g. 'languageConstants/1000' "
                    "for English). Optional; omitting returns ideas for all languages."
                ),
            ),
        ] = None,
        geo_target_constants: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Geo-target constant resource names (e.g. ['geoTargetConstants/2840'] "
                    "for the US). Up to 10. Use suggest_geo_target_constants to look "
                    "these up if you only have country names."
                ),
            ),
        ] = None,
        include_adult_keywords: Annotated[
            bool,
            Field(description="Include adult-content keywords in the results."),
        ] = False,
        page_size: Annotated[
            int,
            Field(
                ge=1,
                le=10000,
                description=(
                    "Page size. Caller is responsible for pagination via the "
                    "next_page_token returned in the result if needed."
                ),
            ),
        ] = 100,
    ) -> dict[str, Any]:
        """Generate keyword ideas for SEM campaign planning.

        Returns Google's keyword-idea result list with avg monthly searches,
        competition, and suggested bids. The standard pre-campaign research
        workflow: feed seed keywords or URLs and get idea expansions.
        """
        params: dict[str, Any] = {
            "include_adult_keywords": include_adult_keywords,
            "page_size": page_size,
        }
        if language is not None:
            params["language"] = language
        if geo_target_constants is not None:
            params["geo_target_constants"] = geo_target_constants

        if seed_type == "keyword":
            if not keywords:
                raise ValidationFailed(
                    "seed_type='keyword' requires the `keywords` argument."
                )
            params["keyword_seed"] = {"keywords": keywords}
        elif seed_type == "url":
            if not url:
                raise ValidationFailed("seed_type='url' requires the `url` argument.")
            params["url_seed"] = {"url": url}
        elif seed_type == "site":
            if not site:
                raise ValidationFailed("seed_type='site' requires the `site` argument.")
            params["site_seed"] = {"site": site}
        else:  # keyword_and_url
            if not keywords or not url:
                raise ValidationFailed(
                    "seed_type='keyword_and_url' requires both `keywords` and `url`."
                )
            params["keyword_and_url_seed"] = {"keywords": keywords, "url": url}

        def go() -> dict[str, Any]:
            check_customer_allowlist(customer_id, allowlist=allowlist)
            response = rpc_impl.invoke(
                client,
                "keyword_plan_idea_service",
                "generate_keyword_ideas",
                params,
                customer_id=customer_id,
                validate_only=None,
            )
            return _proto.message_to_dict(response)

        return await asyncio.to_thread(go)

    # ---------------- async-job dispatchers ---------------------------------

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Manage Google Ads batch job",
            # Dispatcher: create/add/run touch state, status/results don't.
            # destructiveHint=False because the destructive moment is run, and
            # the LLM has already gone through preview/apply for each operation
            # (or explicitly chose to assemble outside the safety flow).
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="batch_job")
    async def batch_job(  # pyright: ignore[reportUnusedFunction]
        customer_id: CustomerIdArg,
        action: Annotated[
            Literal["create", "add_operations", "run", "status", "results"],
            Field(
                description=(
                    "Lifecycle step. 'create' returns a batch_job resource_name; "
                    "'add_operations' appends MutateOperation entries (paged via "
                    "sequence_token); 'run' kicks the job off async; 'status' polls "
                    "via GAQL; 'results' fetches per-op outcomes once status=DONE."
                ),
            ),
        ],
        resource_name: Annotated[
            str | None,
            Field(
                description=(
                    "Batch-job resource_name. Required for every action except "
                    "'create'. Format: 'customers/{customer_id}/batchJobs/{id}'."
                ),
            ),
        ] = None,
        operations: Annotated[
            list[Operation] | None,
            Field(
                description=(
                    "Operations to append. Required for action='add_operations'. "
                    "Same shape as the Layer-2 mutate tool — service, op, "
                    "resource, update_mask."
                ),
            ),
        ] = None,
        sequence_token: Annotated[
            str | None,
            Field(
                description=(
                    "Returned by the previous add_operations call. Pass back to "
                    "append the next page; omit on the first add."
                ),
            ),
        ] = None,
        page_size: Annotated[
            int,
            Field(ge=1, le=10000, description="Page size for action='results'."),
        ] = 1000,
        page_token: Annotated[
            str | None,
            Field(description="Page token for action='results'."),
        ] = None,
    ) -> dict[str, Any]:
        """Manage a Google Ads BatchJob — async-batch lifecycle.

        BatchJob accepts hundreds of MutateOperation entries and runs them
        async on Google's side; large-scale changes (bulk pause, mass label
        updates) go through here rather than the synchronous mutate tool.
        Each lifecycle step is a separate MCP tool call — the LLM composes
        them.
        """
        check_customer_allowlist(customer_id, allowlist=allowlist)

        if action == "create":
            response = await asyncio.to_thread(
                rpc_impl.invoke,
                client,
                "batch_job_service",
                "mutate_batch_job",
                {"operation": {"create": {}}},
                customer_id=customer_id,
                validate_only=None,
            )
            return _proto.message_to_dict(response)

        if resource_name is None:
            raise ValidationFailed(
                f"action='{action}' requires `resource_name`. Get it from "
                "the previous batch_job(action='create') call."
            )

        if action == "add_operations":
            if not operations:
                raise ValidationFailed(
                    "action='add_operations' requires `operations` (a non-empty list)."
                )
            mutate_ops = [_operation_to_dict(op) for op in operations]
            params: dict[str, Any] = {
                "resource_name": resource_name,
                "mutate_operations": mutate_ops,
            }
            if sequence_token is not None:
                params["sequence_token"] = sequence_token
            response = await asyncio.to_thread(
                rpc_impl.invoke,
                client,
                "batch_job_service",
                "add_batch_job_operations",
                params,
                customer_id=None,  # request has no customer_id field
                validate_only=None,
            )
            return _proto.message_to_dict(response)

        if action == "run":
            response = await asyncio.to_thread(
                rpc_impl.invoke,
                client,
                "batch_job_service",
                "run_batch_job",
                {"resource_name": resource_name},
                customer_id=None,
                validate_only=None,
            )
            return _proto.message_to_dict(response)

        if action == "status":
            # batch_job is GAQL-queryable; we wrap the canonical "is it done?"
            # query so the LLM doesn't have to reconstruct it each poll.
            from google_ads_mcp.ads import gaql as _gaql

            query = (
                "SELECT batch_job.resource_name, batch_job.status, "
                "batch_job.metadata.creation_date_time, "
                "batch_job.metadata.start_date_time, "
                "batch_job.metadata.completion_date_time, "
                "batch_job.metadata.estimated_completion_ratio, "
                "batch_job.metadata.operation_count, "
                "batch_job.metadata.executed_operation_count "
                f"FROM batch_job WHERE batch_job.resource_name = '{resource_name}'"
            )
            result = await asyncio.to_thread(
                _gaql.search,
                client,
                customer_id,
                query,
                max_rows=10,
                max_bytes=1 << 16,
            )
            return result.model_dump()

        # action == "results"
        params = {"resource_name": resource_name, "page_size": page_size}
        if page_token is not None:
            params["page_token"] = page_token
        response = await asyncio.to_thread(
            rpc_impl.invoke,
            client,
            "batch_job_service",
            "list_batch_job_results",
            params,
            customer_id=None,
            validate_only=None,
        )
        return _proto.message_to_dict(response)

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Manage offline user data job",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    @with_activity(activity, name="offline_user_data_job")
    async def offline_user_data_job(  # pyright: ignore[reportUnusedFunction]
        customer_id: CustomerIdArg,
        action: Annotated[
            Literal["create", "add_operations", "run", "status"],
            Field(
                description=(
                    "Lifecycle step. 'create' returns a job resource_name; "
                    "'add_operations' appends user-list / store-sales operations; "
                    "'run' kicks off processing; 'status' polls via GAQL."
                ),
            ),
        ],
        resource_name: Annotated[
            str | None,
            Field(
                description=(
                    "Job resource_name. Required for every action except 'create'. "
                    "Format: 'customers/{customer_id}/offlineUserDataJobs/{id}'."
                ),
            ),
        ] = None,
        job: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "Job payload for action='create'. Required. Top-level keys: "
                    "type ('CUSTOMER_MATCH_USER_LIST' | 'STORE_SALES_UPLOAD_*'), "
                    "customer_match_user_list_metadata, store_sales_metadata. "
                    "See gads-rpc-schema://offline_user_data_job_service/"
                    "create_offline_user_data_job for the full shape."
                ),
            ),
        ] = None,
        operations: Annotated[
            list[dict[str, Any]] | None,
            Field(
                description=(
                    "OfflineUserDataJobOperation entries for action='add_operations'. "
                    "Each is a dict with one of: create, remove, remove_all."
                ),
            ),
        ] = None,
        enable_partial_failure: Annotated[
            bool,
            Field(description="For action='add_operations': accept-and-report partial failures."),
        ] = False,
    ) -> dict[str, Any]:
        """Manage a Google Ads OfflineUserDataJob (Customer Match / Store Sales)."""
        check_customer_allowlist(customer_id, allowlist=allowlist)

        if action == "create":
            if job is None:
                raise ValidationFailed(
                    "action='create' requires `job` (the OfflineUserDataJob payload)."
                )
            response = await asyncio.to_thread(
                rpc_impl.invoke,
                client,
                "offline_user_data_job_service",
                "create_offline_user_data_job",
                {"job": job},
                customer_id=customer_id,
                validate_only=None,
            )
            return _proto.message_to_dict(response)

        if resource_name is None:
            raise ValidationFailed(
                f"action='{action}' requires `resource_name`. Get it from "
                "the previous offline_user_data_job(action='create') call."
            )

        if action == "add_operations":
            if not operations:
                raise ValidationFailed(
                    "action='add_operations' requires `operations` (non-empty list)."
                )
            response = await asyncio.to_thread(
                rpc_impl.invoke,
                client,
                "offline_user_data_job_service",
                "add_offline_user_data_job_operations",
                {
                    "resource_name": resource_name,
                    "operations": operations,
                    "enable_partial_failure": enable_partial_failure,
                },
                customer_id=None,
                validate_only=None,
            )
            return _proto.message_to_dict(response)

        if action == "run":
            response = await asyncio.to_thread(
                rpc_impl.invoke,
                client,
                "offline_user_data_job_service",
                "run_offline_user_data_job",
                {"resource_name": resource_name},
                customer_id=None,
                validate_only=None,
            )
            return _proto.message_to_dict(response)

        # action == "status" — GAQL on offline_user_data_job
        from google_ads_mcp.ads import gaql as _gaql

        query = (
            "SELECT offline_user_data_job.resource_name, "
            "offline_user_data_job.status, "
            "offline_user_data_job.failure_reason, "
            "offline_user_data_job.type "
            "FROM offline_user_data_job "
            f"WHERE offline_user_data_job.resource_name = '{resource_name}'"
        )
        result = await asyncio.to_thread(
            _gaql.search,
            client,
            customer_id,
            query,
            max_rows=10,
            max_bytes=1 << 16,
        )
        return result.model_dump()

