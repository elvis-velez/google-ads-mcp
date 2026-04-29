# pyright: basic
"""Generic RPC dispatcher for the Google Ads SDK surface.

`mutate(operations)` covers the 64 services that fit the unified
`MutateOperation` proto. The remaining ~40 services expose RPCs that don't
fit that shape — recommendation apply/dismiss, experiment lifecycle,
keyword ideas, conversion uploads, etc. This module is the single
SDK-aware entry point for all of them, used by the Layer-2 escape hatches
`call_read_rpc` and `call_mutate_rpc`.

How dispatch works:

1. `service` (snake_case, e.g. "recommendation_service") → SDK service
   stub via `client.get_service(StudlyName)`.
2. `method` (snake_case, e.g. "apply_recommendation") → `getattr(stub, method)`.
3. The request proto type is derived from the method name:
   `apply_recommendation` → `ApplyRecommendationRequest`. Resolved via
   `client.get_type(...)`.
4. `params` (a plain dict) is marshalled into the request proto via
   `setattr` per field. Unknown fields raise `ValidationFailed`.
5. `customer_id` and `validate_only` are auto-injected when the request
   proto has those fields.
6. Invocation runs inside `translate_errors(...)` so vendor exceptions
   surface as `ApiError`.

The catalog and request_schema helpers introspect the same SDK to power
the `gads-rpc-catalog://` and `gads-rpc-schema://` discovery resources.
"""

from __future__ import annotations

import importlib
import os
import re
from dataclasses import dataclass
from typing import Any

from google.ads.googleads.client import GoogleAdsClient

from google_ads_mcp.ads._errors import translate_errors
from google_ads_mcp.errors import ValidationFailed
from google_ads_mcp.types import CustomerId

# Snake_case ⇄ StudlyCase. The SDK exposes StudlyCase service / type names
# (`RecommendationService`, `ApplyRecommendationRequest`) but our internal
# wire format and the LLM's mental model are snake_case (matching how
# `Operation.service` already works).


def _to_studly(snake: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in snake.split("_"))


_SNAKE_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _to_snake(studly: str) -> str:
    return _SNAKE_RE.sub("_", studly).lower()


# === Dispatch ==============================================================


def invoke(
    client: GoogleAdsClient,
    service: str,
    method: str,
    params: dict[str, Any],
    *,
    customer_id: CustomerId | None,
    validate_only: bool | None = None,
) -> Any:
    """Call `service.method(params)` against the SDK.

    Returns the raw response proto; callers marshal to dicts via
    `_proto.flatten` / `_proto.coerce` (when a response carries a field_mask)
    or via `proto.Message.to_dict(...)` for single-message responses.

    Raises:
        ValidationFailed — unknown service/method, or unknown field in `params`.
        ApiError — anything the SDK would otherwise raise (translated).
    """
    stub = _resolve_stub(client, service)
    rpc = getattr(stub, method, None)
    if rpc is None or not callable(rpc):
        raise ValidationFailed(
            f"Service '{service}' has no method '{method}'. Use the "
            "gads-rpc-catalog:// resource to discover valid methods."
        )

    request_type_name = _to_studly(method) + "Request"
    try:
        request: Any = client.get_type(request_type_name)
    except (ValueError, AttributeError) as e:
        raise ValidationFailed(
            f"No request type '{request_type_name}' for {service}.{method}. "
            "This method may not follow the standard RPC convention; if it's "
            "a known special case, add a dedicated tool rather than going "
            "through call_*_rpc."
        ) from e

    field_names = _request_field_names(request)

    if customer_id is not None and "customer_id" in field_names:
        params = {"customer_id": customer_id, **params}

    if validate_only is not None and "validate_only" in field_names:
        params = {**params, "validate_only": validate_only}

    _set_request_fields(request, params, allowed=field_names, label=f"{service}.{method}")

    label = f"Rpc[{service}.{method}, customer={customer_id}]"
    with translate_errors(label):
        return rpc(request=request)


def _resolve_stub(client: GoogleAdsClient, service: str) -> Any:
    """Snake_case service name → SDK service stub."""
    try:
        return client.get_service(_to_studly(service))
    except (ValueError, AttributeError) as e:
        raise ValidationFailed(
            f"Unknown Google Ads service '{service}'. Use the "
            "gads-rpc-catalog:// resource to discover valid services."
        ) from e


def _request_field_names(request: Any) -> set[str]:
    """Set of field names defined on the request proto."""
    pb = type(request).pb(request) if hasattr(type(request), "pb") else None
    if pb is None:
        return set()
    return {f.name for f in pb.DESCRIPTOR.fields}


def _set_request_fields(
    request: Any,
    params: dict[str, Any],
    *,
    allowed: set[str],
    label: str,
) -> None:
    """Marshal a dict into a proto-plus message; reject unknown fields."""
    for k, v in params.items():
        if k not in allowed:
            raise ValidationFailed(
                f"Unknown field '{k}' on request for {label}. Allowed: "
                f"{sorted(allowed)}"
            )
        try:
            setattr(request, k, v)
        except (AttributeError, TypeError, ValueError) as e:
            raise ValidationFailed(
                f"Cannot set {label}.{k}={v!r}: {e}"
            ) from e


# === Catalog & schema (introspection) ======================================


@dataclass(frozen=True, slots=True)
class RpcDescriptor:
    """One row of the gads-rpc-catalog:// resource."""

    service: str           # snake_case, e.g. "recommendation_service"
    method: str            # snake_case, e.g. "apply_recommendation"
    read_only: bool        # heuristic: get_*, list_*, search_*, generate_*, suggest_*, fetch_*
    supports_validate_only: bool
    request_type: str      # StudlyCaseRequest, e.g. "ApplyRecommendationRequest"


# Methods on every SDK client class that aren't real RPCs.
_NON_RPC_METHODS: frozenset[str] = frozenset({
    "get_mtls_endpoint_and_cert_source",
    "get_transport_class",
    "common_folder_path",
    "common_billing_account_path",
    "common_organization_path",
    "common_project_path",
    "common_location_path",
})


_READ_PREFIXES: tuple[str, ...] = (
    "get_",
    "list_",
    "search",       # covers search and search_stream
    "generate_",
    "suggest_",
    "fetch_",
)


def looks_read_only(method: str) -> bool:
    """Heuristic: does this method look like a read?

    Used to set the catalog's `read_only` hint and (downstream) to refuse
    `call_read_rpc` invocations against methods that look like writes.
    """
    return method.startswith(_READ_PREFIXES)


def _services_v24_dir() -> str:
    """Path to the v24 services package — the source of truth for catalog."""
    pkg = importlib.import_module("google.ads.googleads.v24.services")
    pkg_file = pkg.__file__
    if pkg_file is None:
        return ""
    return os.path.join(os.path.dirname(pkg_file), "services")


def _client_methods(service_dir_name: str) -> list[str]:
    """All public method names on `<service>.client.<*Client>`."""
    try:
        mod = importlib.import_module(
            f"google.ads.googleads.v24.services.services.{service_dir_name}.client"
        )
    except ImportError:
        return []
    client_cls = None
    for name in dir(mod):
        obj = getattr(mod, name)
        if isinstance(obj, type) and name.endswith("Client") and not name.startswith("_"):
            client_cls = obj
            break
    if client_cls is None:
        return []
    out: list[str] = []
    for n in dir(client_cls):
        if n.startswith("_"):
            continue
        if n in _NON_RPC_METHODS:
            continue
        if n.startswith(("from_", "parse_")) or n.endswith("_path"):
            continue
        attr = getattr(client_cls, n, None)
        if not callable(attr):
            continue
        out.append(n)
    return out


def request_supports_validate_only(client: GoogleAdsClient, method: str) -> bool:
    """True if the request type for this RPC has a `validate_only` field.

    Exposed for the call_mutate_rpc tool: it needs to know up-front whether
    the preview phase can round-trip to the API or must be client-side only.
    """
    try:
        request: Any = client.get_type(_to_studly(method) + "Request")
    except (ValueError, AttributeError):
        return False
    return "validate_only" in _request_field_names(request)


def catalog(client: GoogleAdsClient) -> list[RpcDescriptor]:
    """Enumerate every public RPC on every v24 service.

    Static for the server's lifetime — call once at startup and cache.
    """
    services_dir = _services_v24_dir()
    if not os.path.isdir(services_dir):
        return []

    out: list[RpcDescriptor] = []
    for entry in sorted(os.listdir(services_dir)):
        if entry.startswith("_") or not entry.endswith("_service"):
            continue
        for method in _client_methods(entry):
            request_type = _to_studly(method) + "Request"
            out.append(
                RpcDescriptor(
                    service=entry,
                    method=method,
                    read_only=looks_read_only(method),
                    supports_validate_only=request_supports_validate_only(
                        client, method
                    ),
                    request_type=request_type,
                )
            )
    return out


def request_schema(client: GoogleAdsClient, service: str, method: str) -> dict[str, Any]:
    """Return JSON-Schema-shaped fields for one method's request proto.

    Output:
        {
          "service": "...",
          "method": "...",
          "request_type": "ApplyRecommendationRequest",
          "fields": [
            {"name": "...", "type": "...", "label": "OPTIONAL|REPEATED",
             "message_type": "..." | null, "enum_values": [...] | null},
            ...
          ],
          "oneof_groups": [{"name": "seed", "fields": ["keyword_seed", ...]}, ...]
        }
    """
    # Validate the service exists; surfaces a clean ValidationFailed instead of
    # a get_type ImportError when callers fat-finger the service name.
    _resolve_stub(client, service)

    request_type_name = _to_studly(method) + "Request"
    try:
        request: Any = client.get_type(request_type_name)
    except (ValueError, AttributeError) as e:
        raise ValidationFailed(
            f"No request type '{request_type_name}' for {service}.{method}."
        ) from e

    pb = type(request).pb(request) if hasattr(type(request), "pb") else None
    if pb is None:
        return {
            "service": service,
            "method": method,
            "request_type": request_type_name,
            "fields": [],
            "oneof_groups": [],
        }

    descriptor = pb.DESCRIPTOR
    fields: list[dict[str, Any]] = []
    for f in descriptor.fields:
        fields.append(_field_descriptor_to_dict(f))

    oneof_groups: list[dict[str, Any]] = []
    for o in descriptor.oneofs:
        oneof_groups.append(
            {"name": o.name, "fields": [f.name for f in o.fields]}
        )

    return {
        "service": service,
        "method": method,
        "request_type": request_type_name,
        "fields": fields,
        "oneof_groups": oneof_groups,
    }


# proto FieldDescriptor.TYPE_* values mapped to wire-format names. We avoid
# importing the descriptor module just for these constants — the values are
# stable and documented in the protobuf wire spec.
_TYPE_NAMES: dict[int, str] = {
    1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32",
    6: "fixed64", 7: "fixed32", 8: "bool", 9: "string", 10: "group",
    11: "message", 12: "bytes", 13: "uint32", 14: "enum", 15: "sfixed32",
    16: "sfixed64", 17: "sint32", 18: "sint64",
}
_LABEL_NAMES: dict[int, str] = {1: "OPTIONAL", 2: "REQUIRED", 3: "REPEATED"}


def _field_descriptor_to_dict(f: Any) -> dict[str, Any]:
    """One proto field → JSON-Schema-ish dict."""
    out: dict[str, Any] = {
        "name": f.name,
        "type": _TYPE_NAMES.get(f.type, str(f.type)),
        "label": _LABEL_NAMES.get(f.label, str(f.label)),
        "message_type": None,
        "enum_values": None,
    }
    if f.message_type is not None:
        out["message_type"] = f.message_type.full_name
    if f.enum_type is not None:
        out["enum_values"] = [v.name for v in f.enum_type.values]
    return out
