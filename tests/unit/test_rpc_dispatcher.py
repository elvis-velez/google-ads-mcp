# pyright: reportArgumentType=false, reportPrivateUsage=false
"""Unit tests for `ads.rpc` — dispatcher, catalog, schema helpers.

The dispatcher's contract is:
1. Snake_case service/method names → SDK service stub + method
2. params (dict) → request proto via setattr; unknown keys rejected
3. customer_id and validate_only auto-injected when the request has those fields
4. Vendor exceptions translate to ApiError; unknown service/method → ValidationFailed

We mock the SDK client at the seam so unit tests run without credentials.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from google_ads_mcp.ads import rpc as rpc_impl
from google_ads_mcp.errors import ValidationFailed


class _FakeRequest:
    """Minimal proto-plus-shaped request. Records what was set on it."""

    _FIELDS: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.set: dict[str, Any] = {}

    @classmethod
    def pb(cls, _instance: Any) -> Any:
        # Mimic proto-plus's `pb()` returning the underlying protobuf descriptor.
        fields = [SimpleNamespace(name=f) for f in cls._FIELDS]
        descriptor = SimpleNamespace(fields=fields)
        return SimpleNamespace(DESCRIPTOR=descriptor)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "set":
            object.__setattr__(self, name, value)
            return
        if name not in type(self)._FIELDS:
            raise AttributeError(f"unknown field {name}")
        self.set[name] = value


class _RecRequest(_FakeRequest):
    _FIELDS = ("customer_id", "resource_name", "validate_only")


class _IdeasRequest(_FakeRequest):
    _FIELDS = ("customer_id", "language", "geo_target_constants", "page_size")


class _FakeClient:
    """Stand-in for GoogleAdsClient. Only `get_service` and `get_type` matter."""

    def __init__(
        self,
        services: dict[str, Any] | None = None,
        types: dict[str, type[_FakeRequest]] | None = None,
    ) -> None:
        self._services = services or {}
        self._types = types or {}
        self.calls: list[tuple[str, str, _FakeRequest]] = []

    def get_service(self, name: str) -> Any:
        if name not in self._services:
            raise ValueError(f"unknown service {name}")
        return self._services[name]

    def get_type(self, name: str) -> Any:
        if name not in self._types:
            raise ValueError(f"unknown type {name}")
        return self._types[name]()


def _service_stub(client: _FakeClient, service_name: str, method_name: str) -> Any:
    """Build a stub that captures invocation."""
    def method(request: Any) -> Any:
        client.calls.append((service_name, method_name, request))
        return SimpleNamespace(ok=True)

    return SimpleNamespace(**{method_name: method})


def test_invoke_dispatches_and_marshals_params() -> None:
    client = _FakeClient(
        services={
            "RecommendationService": _service_stub(
                _FakeClient.__new__(_FakeClient),  # placeholder; replaced below
                "RecommendationService",
                "apply_recommendation",
            ),
        },
        types={"ApplyRecommendationRequest": _RecRequest},
    )
    # Re-bind so the stub records onto the *real* client we'll inspect.
    client._services["RecommendationService"] = _service_stub(
        client, "RecommendationService", "apply_recommendation"
    )

    rpc_impl.invoke(
        client,
        "recommendation_service",
        "apply_recommendation",
        {"resource_name": "customers/1/recommendations/abc"},
        customer_id="1234567890",
        validate_only=None,
    )

    assert len(client.calls) == 1
    _, _, req = client.calls[0]
    assert isinstance(req, _RecRequest)
    assert req.set == {
        "customer_id": "1234567890",  # auto-injected
        "resource_name": "customers/1/recommendations/abc",
    }
    # validate_only=None means "don't inject" — leaves the field unset.
    assert "validate_only" not in req.set


def test_invoke_injects_validate_only_when_supported() -> None:
    client = _FakeClient(types={"ApplyRecommendationRequest": _RecRequest})
    client._services["RecommendationService"] = _service_stub(
        client, "RecommendationService", "apply_recommendation"
    )

    rpc_impl.invoke(
        client,
        "recommendation_service",
        "apply_recommendation",
        {"resource_name": "x"},
        customer_id="1",
        validate_only=True,
    )

    _, _, req = client.calls[0]
    assert req.set["validate_only"] is True


def test_invoke_rejects_unknown_field() -> None:
    client = _FakeClient(types={"GenerateKeywordIdeasRequest": _IdeasRequest})
    client._services["KeywordPlanIdeaService"] = _service_stub(
        client, "KeywordPlanIdeaService", "generate_keyword_ideas"
    )

    with pytest.raises(ValidationFailed, match="Unknown field 'bogus'"):
        rpc_impl.invoke(
            client,
            "keyword_plan_idea_service",
            "generate_keyword_ideas",
            {"bogus": "value"},
            customer_id="1",
            validate_only=None,
        )


def test_invoke_rejects_unknown_service() -> None:
    client = _FakeClient()

    with pytest.raises(ValidationFailed, match="Unknown Google Ads service 'no_such_service'"):
        rpc_impl.invoke(
            client,
            "no_such_service",
            "do_something",
            {},
            customer_id="1",
            validate_only=None,
        )


def test_invoke_rejects_unknown_method_on_known_service() -> None:
    client = _FakeClient()
    client._services["RecommendationService"] = SimpleNamespace()  # no methods

    with pytest.raises(ValidationFailed, match="has no method 'apply_recommendation'"):
        rpc_impl.invoke(
            client,
            "recommendation_service",
            "apply_recommendation",
            {},
            customer_id="1",
            validate_only=None,
        )


def test_looks_read_only_heuristic() -> None:
    assert rpc_impl.looks_read_only("generate_keyword_ideas") is True
    assert rpc_impl.looks_read_only("list_invoices") is True
    assert rpc_impl.looks_read_only("search_stream") is True
    assert rpc_impl.looks_read_only("get_smart_campaign_status") is True
    assert rpc_impl.looks_read_only("suggest_brands") is True
    assert rpc_impl.looks_read_only("fetch_incentive") is True

    assert rpc_impl.looks_read_only("apply_recommendation") is False
    assert rpc_impl.looks_read_only("promote_experiment") is False
    assert rpc_impl.looks_read_only("upload_click_conversions") is False
    assert rpc_impl.looks_read_only("create_customer_client") is False


def test_catalog_enumerates_real_services() -> None:
    """The catalog walks the real SDK; it should surface known services & methods."""
    # We pass a minimal client because catalog() only needs the SDK package layout
    # and a `get_type` for validate_only detection. Use a real-ish client mock that
    # returns request types via dynamic import.
    class _IntrospectingClient:
        def get_type(self, name: str) -> Any:
            # Real SDK request types — we just want the proto fields, not actual gRPC.
            from importlib import import_module
            for mod_name in (
                "google.ads.googleads.v24.services.types.recommendation_service",
                "google.ads.googleads.v24.services.types.keyword_plan_idea_service",
                "google.ads.googleads.v24.services.types.invoice_service",
                "google.ads.googleads.v24.services.types.experiment_service",
                "google.ads.googleads.v24.services.types.google_ads_service",
            ):
                try:
                    m = import_module(mod_name)
                    if hasattr(m, name):
                        return getattr(m, name)()
                except ImportError:
                    continue
            raise ValueError(name)

    descriptors = rpc_impl.catalog(_IntrospectingClient())  # type: ignore[arg-type]
    names = {(d.service, d.method) for d in descriptors}

    # Spot-checks from each major category.
    assert ("recommendation_service", "apply_recommendation") in names
    assert ("recommendation_service", "dismiss_recommendation") in names
    assert ("keyword_plan_idea_service", "generate_keyword_ideas") in names
    assert ("invoice_service", "list_invoices") in names
    assert ("experiment_service", "promote_experiment") in names

    # The unified mutate is reachable via google_ads_service.mutate too.
    assert ("google_ads_service", "mutate") in names


def test_catalog_marks_read_only_methods() -> None:
    class _Client:
        def get_type(self, _: str) -> Any:
            raise ValueError("not needed for this test")

    descriptors = rpc_impl.catalog(_Client())  # type: ignore[arg-type]
    by_method = {(d.service, d.method): d for d in descriptors}

    assert by_method[
        ("keyword_plan_idea_service", "generate_keyword_ideas")
    ].read_only is True
    assert by_method[
        ("recommendation_service", "apply_recommendation")
    ].read_only is False
    assert by_method[
        ("invoice_service", "list_invoices")
    ].read_only is True
