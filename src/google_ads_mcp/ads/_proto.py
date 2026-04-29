# pyright: basic
"""Shared proto-to-Python helpers.

Both `gaql.py` (response rows) and `rpc.py` (response messages) need to
walk vendor proto values into LLM-friendly plain dicts. The walk and the
value-coercion rules are identical, so they live here once.

Anything else proto-shaped that other `ads/` modules need to flatten or
coerce should land here, not be re-implemented.
"""

from __future__ import annotations

import enum
from typing import Any

import proto
from google.protobuf.json_format import MessageToDict


def flatten(proto_row: Any, paths: list[str]) -> dict[str, Any]:
    """Walk each dotted field path into `proto_row`, building a flat dict.

    Used by GAQL to project the requested SELECT fields, and by the RPC
    response marshaller when a response carries a `field_mask`.
    """
    out: dict[str, Any] = {}
    for path in paths:
        value: Any = proto_row
        for part in path.split("."):
            value = getattr(value, part, None)
            if value is None:
                break
        out[path] = coerce(value)
    return out


def coerce(value: Any) -> Any:
    """Normalise SDK values for JSON-friendly downstream consumption.

    proto-plus enums are IntEnum-like; we report `.name` so the LLM sees
    `"ENABLED"` not `2`. proto-plus repeated/map collections wrap composite
    sub-messages and aren't JSON-serializable as-is — we materialise them
    as plain lists/dicts and recurse. Nested proto-plus Messages get the
    same `Message.to_dict` treatment as top-level responses.

    Everything else is already a Python primitive.
    """
    if value is None:
        return None
    if isinstance(value, enum.Enum):
        return value.name
    # proto-plus repeated collections (RepeatedComposite, RepeatedScalar) —
    # imported lazily by class name to avoid coupling tests to the
    # proto-plus internal layout.
    cls_name = type(value).__name__
    # `Repeated` is the proto-plus base class for repeated proto fields;
    # `RepeatedComposite` and `RepeatedScalar` are its subclasses but
    # in practice the marshalled instance reports as the base in some
    # SDK code paths. Catch all three names.
    if cls_name in ("Repeated", "RepeatedComposite", "RepeatedScalar"):
        return [coerce(item) for item in value]
    if cls_name == "MapComposite":
        return {k: coerce(v) for k, v in value.items()}
    if isinstance(value, proto.Message):
        return type(value).to_dict(value, use_integers_for_enums=False)
    return value


def approximate_size(row: dict[str, Any]) -> int:
    """Rough byte cost of a row when rendered. Used by GAQL to enforce caps."""
    return sum(len(k) + len(repr(v)) for k, v in row.items())


def message_to_dict(message: Any) -> dict[str, Any]:
    """Marshal a Google Ads response into a plain JSON-friendly dict.

    Four response shapes appear in the SDK and have to be handled:

    1. Plain proto-plus `Message` — `recommendation_service.apply_recommendation`,
       `payments_account_service.list_payments_accounts`, etc. Use proto-plus's
       own `Message.to_dict` so enum→name conversion happens natively without
       reaching for `.DESCRIPTOR` (which proto-plus's `__getattr__` rejects).

    2. Pager wrappers — `keyword_plan_idea_service.generate_keyword_ideas`,
       `batch_job_service.list_batch_job_results`, anywhere the API paginates.
       Pagers expose `._response` holding the underlying proto-plus Message.

    3. Long-running `google.api_core.operation.Operation` — returned by
       async APIs like `batch_job_service.run_batch_job`. Its `.operation`
       attribute is a raw `google.longrunning.Operation` protobuf with the
       op name, done flag, and metadata. We don't poll for completion here
       (callers query progress via GAQL on the underlying resource); we
       just snapshot the returned operation metadata.

    4. Raw `google.protobuf.Message` — rare in proto-plus-mode SDKs but
       possible. Fall through to `MessageToDict` directly.
    """
    if isinstance(message, proto.Message):
        return type(message).to_dict(message, use_integers_for_enums=False)

    underlying = getattr(message, "_response", None)
    if isinstance(underlying, proto.Message):
        return type(underlying).to_dict(underlying, use_integers_for_enums=False)

    # Long-running operation wrapper — the .operation attribute is a raw
    # google.longrunning.Operation proto. Detect by the DESCRIPTOR presence
    # on .operation (proto-plus messages have already been short-circuited
    # in case 1, so we won't accidentally trip __getattr__ here).
    inner_op = getattr(message, "operation", None)
    if inner_op is not None and hasattr(inner_op, "DESCRIPTOR"):
        return MessageToDict(
            inner_op,
            preserving_proto_field_name=True,
            use_integers_for_enums=False,
        )

    return MessageToDict(
        message,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )
