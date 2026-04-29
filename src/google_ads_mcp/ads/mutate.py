# pyright: basic
"""Generic mutate via `GoogleAdsService`.

Cross-service atomic call: all operations in one batch succeed or all fail
(`partial_failure` deferred). The translator works by convention against
the `MutateOperation` oneof: every Google Ads service exposes a
`{service}_operation` field whose value has the standard
`create | update | remove` oneof plus an `update_mask` for updates. Adding
support for a new service is "make sure the service name is correct" —
no per-service translator code needed unless that service breaks the
convention.
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
    """Translate an internal Operation to a MutateOperation proto.

    Works against any service whose `{service}_operation` field follows the
    standard create/update/remove + update_mask shape — the overwhelming
    majority of Google Ads services. If a service breaks the convention
    (none currently in v1's working set), add a special case here.
    """
    mutate_op: Any = client.get_type("MutateOperation")

    field_name = f"{op.service}_operation"
    service_op = getattr(mutate_op, field_name, None)
    if service_op is None:
        raise ValidationFailed(
            f"Unknown Google Ads service '{op.service}'. The MutateOperation "
            f"proto has no '{field_name}' field. Use the gads-schema:// "
            "resource to discover valid service names."
        )

    if op.op == "remove":
        rn = op.resource.get("resource_name")
        if not rn:
            raise ValidationFailed(
                f"Remove operation on '{op.service}' requires 'resource_name'."
            )
        service_op.remove = rn
        return mutate_op

    target = service_op.create if op.op == "create" else service_op.update

    for k, v in op.resource.items():
        try:
            setattr(target, k, v)
        except (AttributeError, TypeError, ValueError, KeyError) as e:
            # KeyError surfaces from proto-plus enum lookup when the value
            # isn't a valid enum name (e.g. status="BANANA" → KeyError("BANANA")).
            # Without explicit handling it leaks past as just "'BANANA'".
            raise ValidationFailed(
                f"Cannot set {op.service}.{k}={v!r}: {e}"
            ) from e

    if op.op == "update":
        if not op.update_mask:
            raise ValidationFailed(
                f"Update operation on '{op.service}' requires update_mask "
                "listing the fields being changed."
            )
        for path in op.update_mask:
            service_op.update_mask.paths.append(path)

    return mutate_op


def _extract_resource_name(mutate_op_response: Any) -> str:
    """Pull resource_name from whichever per-service result oneof is set.

    `WhichOneof` lives on the raw protobuf message, not the proto-plus
    wrapper that `use_proto_plus=True` returns. Reach through `_pb` to get
    at it; proto-plus surfaces this as the canonical escape hatch for
    proto-2/3 APIs that the wrapper doesn't expose.
    """
    raw = getattr(mutate_op_response, "_pb", mutate_op_response)
    which = raw.WhichOneof("response")
    if which is None:
        return ""
    sub = getattr(mutate_op_response, which, None)
    if sub is None:
        return ""
    return getattr(sub, "resource_name", "") or ""
