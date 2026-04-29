"""Per-account threshold overrides.

`~/.config/google-ads-mcp/limits.yaml` lets operators raise the global
default caps for specific accounts (e.g. legal/finance verticals where
$200 CPCs are normal). Format:

    defaults:
      cpc_max_micros: 50000000
      budget_max_daily_micros: 1000000000

    per_account:
      "1234567890":
        cpc_max_micros: 200000000

Either section is optional. Any key omitted from a per-account block
inherits from `defaults`; any key omitted from `defaults` inherits from
the values seeded into LimitsConfig (typically Settings's compiled-in
defaults).

The file is read once at server start. Edit + restart to apply changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from google_ads_mcp.errors import ConfigError


@dataclass(frozen=True, slots=True)
class Limits:
    """Resolved threshold caps for one customer."""

    cpc_max_micros: int
    budget_max_daily_micros: int


class _PartialLimits(BaseModel):
    """File-side schema; all fields optional so per-account overrides can be partial."""

    model_config = ConfigDict(extra="forbid")

    cpc_max_micros: int | None = None
    budget_max_daily_micros: int | None = None


class _LimitsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    defaults: _PartialLimits = _PartialLimits()
    per_account: dict[str, _PartialLimits] = {}


class LimitsConfig:
    """Resolves per-customer limits from compiled defaults + a YAML overlay."""

    def __init__(
        self,
        *,
        defaults: Limits,
        per_account: dict[str, _PartialLimits] | None = None,
    ) -> None:
        self._defaults = defaults
        self._per_account = per_account or {}

    def for_customer(self, customer_id: str) -> Limits:
        override = self._per_account.get(customer_id)
        if override is None:
            return self._defaults
        return Limits(
            cpc_max_micros=(
                override.cpc_max_micros
                if override.cpc_max_micros is not None
                else self._defaults.cpc_max_micros
            ),
            budget_max_daily_micros=(
                override.budget_max_daily_micros
                if override.budget_max_daily_micros is not None
                else self._defaults.budget_max_daily_micros
            ),
        )


def load_limits(path: Path, *, baseline: Limits) -> LimitsConfig:
    """Build a LimitsConfig from `path` if present; fall back to baseline only.

    Missing file → baseline-only config (no per-account overrides). Malformed
    file → ConfigError so the operator notices at startup rather than getting
    silently wrong limits at runtime.
    """
    if not path.exists():
        return LimitsConfig(defaults=baseline, per_account={})

    try:
        raw: Any = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"Limits file at {path} is not valid YAML: {e}") from e

    if raw is None:
        # Empty file is fine.
        return LimitsConfig(defaults=baseline, per_account={})
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Limits file at {path} must be a YAML mapping, got {type(raw).__name__}"
        )

    try:
        parsed = _LimitsFile.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"Limits file at {path} is malformed: {e}") from e

    defaults = Limits(
        cpc_max_micros=(
            parsed.defaults.cpc_max_micros
            if parsed.defaults.cpc_max_micros is not None
            else baseline.cpc_max_micros
        ),
        budget_max_daily_micros=(
            parsed.defaults.budget_max_daily_micros
            if parsed.defaults.budget_max_daily_micros is not None
            else baseline.budget_max_daily_micros
        ),
    )
    return LimitsConfig(defaults=defaults, per_account=parsed.per_account)
