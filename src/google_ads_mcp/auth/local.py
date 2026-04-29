"""File-backed credentials provider.

Reads `credentials.yaml` from disk and validates it. The file is created
by the `init` command (Phase 1) with mode 0600. We don't enforce mode at
read time — that's `init`'s job and a chmod failure shouldn't block reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from google_ads_mcp.auth.credentials import Credentials, CredentialsProvider
from google_ads_mcp.errors import ConfigError


class _CredentialsFile(BaseModel):
    """Schema for credentials.yaml. Pydantic gives us a clean error message
    when fields are missing or wrong-typed."""

    developer_token: str
    oauth_client_id: str
    oauth_client_secret: str
    refresh_token: str
    login_customer_id: str | None = None


class LocalRefreshTokenCredentials(CredentialsProvider):
    """Loads credentials from a YAML file on first call, caches afterwards."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._cached: Credentials | None = None

    def get(self) -> Credentials:
        if self._cached is not None:
            return self._cached
        self._cached = self._load()
        return self._cached

    def _load(self) -> Credentials:
        if not self._path.exists():
            raise ConfigError(
                f"Credentials file not found at {self._path}. "
                "Run `google-ads-mcp init` to create one."
            )

        try:
            raw: Any = yaml.safe_load(self._path.read_text())
        except yaml.YAMLError as e:
            raise ConfigError(
                f"Credentials file at {self._path} is not valid YAML: {e}"
            ) from e

        if not isinstance(raw, dict):
            raise ConfigError(
                f"Credentials file at {self._path} must be a YAML mapping, "
                f"got {type(raw).__name__}"
            )

        try:
            parsed = _CredentialsFile.model_validate(raw)
        except ValidationError as e:
            raise ConfigError(
                f"Credentials file at {self._path} is missing or malformed: {e}"
            ) from e

        return Credentials(
            developer_token=parsed.developer_token,
            oauth_client_id=parsed.oauth_client_id,
            oauth_client_secret=parsed.oauth_client_secret,
            refresh_token=parsed.refresh_token,
            login_customer_id=parsed.login_customer_id,
        )
