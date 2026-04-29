# pyright: reportPrivateUsage=false
"""Vendor-error translation tests.

The boundary's whole job is making sure no vendor exception leaks upward.
Both Google Ads SDK families (`GoogleAdsException` and `GoogleAPICallError`)
must be caught and remapped to `ApiError`.
"""

from __future__ import annotations

import pytest
from google.api_core.exceptions import PermissionDenied

from google_ads_mcp.ads._errors import translate_errors
from google_ads_mcp.errors import ApiError


def test_translates_google_api_call_error() -> None:
    """gRPC-level errors (e.g. PermissionDenied for un-enabled API) become
    ApiError, not raw gRPC tracebacks."""
    with (
        pytest.raises(ApiError, match="ListAccessibleCustomers failed"),
        translate_errors("ListAccessibleCustomers"),
    ):
        raise PermissionDenied("Google Ads API has not been used in project X.")


def test_passes_other_exceptions_through() -> None:
    """Non-vendor exceptions are not caught; they propagate unchanged so we
    don't accidentally swallow programming errors."""
    with pytest.raises(ValueError, match="not vendor"), translate_errors("op"):
        raise ValueError("not vendor")


def test_includes_op_label_in_message() -> None:
    """The op label appears in the wrapped error message so logs and
    surfaced UX point at the right SDK call."""
    with (
        pytest.raises(ApiError, match=r"GoogleAdsFieldService\[campaign\] failed"),
        translate_errors("GoogleAdsFieldService[campaign]"),
    ):
        raise PermissionDenied("disabled")
