# pyright: basic
"""Generic mutate via `GoogleAdsService`.

Cross-service atomic call: all operations in one batch succeed or all fail
(unless we eventually expose `partial_failure`, deferred). v1 supports
`campaign` create/update/remove; other services raise `NotImplementedError`
that points operators at the appropriate Layer-1 outcome tool.

Building a `MutateOperation` proto requires fiddly proto-plus assignment;
keeping it scoped to one service in v1 lets us prove the safety machinery
without expanding here. Phase 3 outcome tools will pull in additional
service translators as they need them — one helper per service, same
shape as `_build_campaign_op`.
"""

from __future__ import annotations

from typing import Any

from google.ads.googleads.client import GoogleAdsClient

from google_ads_mcp.ads._errors import translate_errors
from google_ads_mcp.errors import ValidationFailed
from google_ads_mcp.types import CustomerId, Operation


def mutate(
    client: GoogleAdsClient,
    customer_id: CustomerId,
    operations: list[Operation],
    *,
    validate_only: bool,
) -> list[str]:
    """Run a multi-service mutate; return resource_names from the response.

    For validate_only=True the response typically contains empty
    resource_names (the API returns no IDs for un-applied operations) — that's
    expected, not a contract failure.
    """
    service = client.get_service("GoogleAdsService")
    mutate_ops = [_build_mutate_operation(client, op) for op in operations]

    request: Any = client.get_type("MutateGoogleAdsRequest")
    request.customer_id = customer_id
    request.mutate_operations.extend(mutate_ops)
    request.validate_only = validate_only

    label = (
        f"Mutate[customer={customer_id}, n={len(operations)}, "
        f"validate_only={validate_only}]"
    )
    with translate_errors(label):
        response = service.mutate(request=request)

    return [_extract_resource_name(r) for r in response.mutate_operation_responses]


def _build_mutate_operation(client: GoogleAdsClient, op: Operation) -> Any:
    if op.service == "campaign":
        return _build_campaign_op(client, op)
    raise NotImplementedError(
        f"Layer-2 mutate doesn't yet support service '{op.service}'. "
        "Use a Layer-1 outcome tool if available, or extend ads/mutate.py "
        "with a per-service translator following the campaign pattern."
    )


def _build_campaign_op(client: GoogleAdsClient, op: Operation) -> Any:
    mutate_op: Any = client.get_type("MutateOperation")
    campaign_op = mutate_op.campaign_operation

    if op.op == "remove":
        rn = op.resource.get("resource_name")
        if not rn:
            raise ValidationFailed(
                "Remove operation requires 'resource_name' in resource."
            )
        campaign_op.remove = rn
        return mutate_op

    target = campaign_op.create if op.op == "create" else campaign_op.update

    for k, v in op.resource.items():
        try:
            setattr(target, k, v)
        except (AttributeError, TypeError, ValueError) as e:
            raise ValidationFailed(
                f"Cannot set campaign.{k}={v!r}: {e}"
            ) from e

    if op.op == "update":
        if not op.update_mask:
            raise ValidationFailed(
                "Update operation requires update_mask listing the fields being changed."
            )
        for path in op.update_mask:
            campaign_op.update_mask.paths.append(path)

    return mutate_op


def _extract_resource_name(mutate_op_response: Any) -> str:
    """Pull resource_name from whichever per-service result oneof is set."""
    which = mutate_op_response.WhichOneof("response")
    if which is None:
        return ""
    sub = getattr(mutate_op_response, which, None)
    if sub is None:
        return ""
    return getattr(sub, "resource_name", "") or ""
