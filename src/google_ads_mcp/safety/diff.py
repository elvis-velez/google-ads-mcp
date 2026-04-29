"""Operation → OperationDiff renderer.

The Google Ads API's `validate_only=true` returns yes/no plus policy
findings but no diff. So we render our own — the LLM (and any human
reviewing) sees a structured human-readable preview before committing.

v1 renders the operation payload itself: clear and accurate but doesn't
fetch current entity state for UPDATE ops. Phase 2.5 / 3 may add a
"fetch current via GAQL, render before/after" mode keyed on resource type.
"""

from __future__ import annotations

from typing import Any, cast

from google_ads_mcp.types import Operation, OperationDiff, RpcCall, RpcCallDiff


def render(op: Operation) -> OperationDiff:
    """Build a human-readable preview of `op`."""
    if op.op == "remove":
        return _render_remove(op)
    if op.op == "create":
        return _render_create(op)
    return _render_update(op)


def _render_remove(op: Operation) -> OperationDiff:
    rn = op.resource.get("resource_name", "<missing resource_name>")
    return OperationDiff(
        service=op.service,
        op="remove",
        summary=f"remove {op.service} {rn}",
        detail=f"Will remove {op.service} resource: {rn}",
    )


def _render_create(op: Operation) -> OperationDiff:
    lines = [f"Will create {op.service} with:"]
    lines.extend(f"  {k}: {_render_value(v)}" for k, v in sorted(op.resource.items()))
    return OperationDiff(
        service=op.service,
        op="create",
        summary=f"create {op.service}",
        detail="\n".join(lines),
    )


def _render_update(op: Operation) -> OperationDiff:
    rn = op.resource.get("resource_name", "<missing resource_name>")
    lines: list[str] = [f"Will update {op.service} {rn}"]
    if op.update_mask:
        lines.append("masked fields:")
        for path in op.update_mask:
            value = _resolve_path(op.resource, path)
            rendered = _render_value(value) if value is not _MISSING else "<not in payload>"
            lines.append(f"  {path}: {rendered}")
    else:
        lines.append("(no update_mask supplied — the API will reject this)")
    return OperationDiff(
        service=op.service,
        op="update",
        summary=f"update {op.service} {rn}",
        detail="\n".join(lines),
    )


_MISSING: Any = object()


def _resolve_path(resource: dict[str, Any], path: str) -> Any:
    """Resolve a dotted update_mask path against a resource dict.

    Tries the full path as a flat key first (covers the common single-segment
    case like 'status'), then walks nested dicts segment-by-segment for
    paths like 'target_spend.target_spend_micros'. Returns `_MISSING` when
    no value resolves so the caller can render `<not in payload>`.
    """
    if path in resource:
        return resource[path]
    value: Any = resource
    for part in path.split("."):
        if not isinstance(value, dict):
            return _MISSING
        nested = cast("dict[str, Any]", value)
        if part not in nested:
            return _MISSING
        value = nested[part]
    return value


def _render_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return repr(value)


def render_rpc_call(rpc: RpcCall) -> RpcCallDiff:
    """Build a human-readable preview of an `RpcCall`.

    Shape parallels `render(Operation)`: a one-line summary plus a multi-line
    detail showing the params being sent. The diff is client-side only — the
    actual API round-trip happens in the perform_mutate_rpc flow when the
    request type supports validate_only.
    """
    lines = [f"Will call {rpc.service}.{rpc.method} with:"]
    if not rpc.params:
        lines.append("  (no params)")
    else:
        for k, v in sorted(rpc.params.items()):
            lines.append(f"  {k}: {_render_value(v)}")
    return RpcCallDiff(
        service=rpc.service,
        method=rpc.method,
        summary=f"rpc {rpc.service}.{rpc.method}",
        detail="\n".join(lines),
    )
