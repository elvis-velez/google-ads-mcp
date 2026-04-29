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
    `"ENABLED"` not `2`. Everything else is already a Python primitive.
    """
    if value is None:
        return None
    if isinstance(value, enum.Enum):
        return value.name
    return value


def approximate_size(row: dict[str, Any]) -> int:
    """Rough byte cost of a row when rendered. Used by GAQL to enforce caps."""
    return sum(len(k) + len(repr(v)) for k, v in row.items())


def message_to_dict(message: Any) -> dict[str, Any]:
    """Marshal a Google Ads response proto (proto-plus) into a plain dict.

    Used by the RPC dispatcher to turn arbitrary response messages into
    LLM-friendly JSON. proto-plus messages wrap an underlying google.protobuf
    Message accessible via `type(msg).pb(msg)`; we pass that through
    `MessageToDict` so enum values come back as names (`"ENABLED"` not `2`)
    and field names use the proto convention (snake_case).
    """
    pb_method = getattr(type(message), "pb", None)
    pb: Any = pb_method(message) if callable(pb_method) else message
    return MessageToDict(
        pb,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )
