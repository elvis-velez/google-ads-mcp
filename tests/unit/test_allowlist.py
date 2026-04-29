"""Tests for the customer-id allowlist cache + the matching guardrail."""

from __future__ import annotations

import pytest

from google_ads_mcp.errors import GuardrailViolation
from google_ads_mcp.safety.allowlist import CustomerAllowlist, check_customer_allowlist


def test_caches_after_first_fetch() -> None:
    calls = 0

    def fetch() -> list[str]:
        nonlocal calls
        calls += 1
        return ["1234567890", "9876543210"]

    allowlist = CustomerAllowlist(fetch=fetch)

    assert allowlist.is_allowed("1234567890")
    assert allowlist.is_allowed("9876543210")
    assert allowlist.all() == ["1234567890", "9876543210"]
    assert calls == 1


def test_is_allowed_for_known_id() -> None:
    allowlist = CustomerAllowlist(fetch=lambda: ["1234567890"])
    assert allowlist.is_allowed("1234567890")


def test_is_not_allowed_for_unknown_id() -> None:
    allowlist = CustomerAllowlist(fetch=lambda: ["1234567890"])
    assert not allowlist.is_allowed("9999999999")


def test_guardrail_passes_for_allowed_id() -> None:
    allowlist = CustomerAllowlist(fetch=lambda: ["1234567890"])
    check_customer_allowlist("1234567890", allowlist=allowlist)


def test_guardrail_rejects_unknown_id() -> None:
    allowlist = CustomerAllowlist(fetch=lambda: ["1234567890"])
    with pytest.raises(GuardrailViolation, match="not accessible"):
        check_customer_allowlist("9999999999", allowlist=allowlist)


def test_all_returns_sorted_copy() -> None:
    allowlist = CustomerAllowlist(fetch=lambda: ["3333333333", "1111111111", "2222222222"])
    assert allowlist.all() == ["1111111111", "2222222222", "3333333333"]
