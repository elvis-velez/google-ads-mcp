"""Tests for per-account threshold overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from google_ads_mcp.errors import ConfigError
from google_ads_mcp.safety.limits import Limits, load_limits

BASELINE = Limits(cpc_max_micros=50_000_000, budget_max_daily_micros=1_000_000_000)


def test_missing_file_returns_baseline_only(tmp_path: Path) -> None:
    cfg = load_limits(tmp_path / "no-limits.yaml", baseline=BASELINE)

    assert cfg.for_customer("anyone") == BASELINE


def test_empty_file_returns_baseline_only(tmp_path: Path) -> None:
    path = tmp_path / "limits.yaml"
    path.write_text("")
    cfg = load_limits(path, baseline=BASELINE)

    assert cfg.for_customer("anyone") == BASELINE


def test_defaults_override_baseline(tmp_path: Path) -> None:
    path = tmp_path / "limits.yaml"
    path.write_text(
        """\
defaults:
  cpc_max_micros: 100000000
"""
    )
    cfg = load_limits(path, baseline=BASELINE)

    resolved = cfg.for_customer("anyone")
    assert resolved.cpc_max_micros == 100_000_000  # from file
    assert resolved.budget_max_daily_micros == 1_000_000_000  # from baseline


def test_per_account_partial_override(tmp_path: Path) -> None:
    path = tmp_path / "limits.yaml"
    path.write_text(
        """\
per_account:
  "1234567890":
    cpc_max_micros: 200000000
"""
    )
    cfg = load_limits(path, baseline=BASELINE)

    target = cfg.for_customer("1234567890")
    assert target.cpc_max_micros == 200_000_000  # account override
    assert target.budget_max_daily_micros == 1_000_000_000  # baseline

    other = cfg.for_customer("9999999999")
    assert other == BASELINE  # untouched account uses baseline


def test_per_account_inherits_from_overridden_defaults(tmp_path: Path) -> None:
    path = tmp_path / "limits.yaml"
    path.write_text(
        """\
defaults:
  cpc_max_micros: 80000000
per_account:
  "1234567890":
    budget_max_daily_micros: 5000000000
"""
    )
    cfg = load_limits(path, baseline=BASELINE)

    target = cfg.for_customer("1234567890")
    # cpc inherits from file's defaults section (80M), not baseline (50M).
    assert target.cpc_max_micros == 80_000_000
    assert target.budget_max_daily_micros == 5_000_000_000


def test_unknown_field_rejected(tmp_path: Path) -> None:
    path = tmp_path / "limits.yaml"
    path.write_text(
        """\
defaults:
  some_unknown_cap: 999
"""
    )
    with pytest.raises(ConfigError, match="malformed"):
        load_limits(path, baseline=BASELINE)


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "limits.yaml"
    path.write_text("defaults: [unterminated")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_limits(path, baseline=BASELINE)


def test_non_mapping_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "limits.yaml"
    path.write_text("- a list at root\n")
    with pytest.raises(ConfigError, match="must be a YAML mapping"):
        load_limits(path, baseline=BASELINE)
