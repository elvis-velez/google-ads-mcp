"""Diagnostic logging — server lifecycle and operator-facing warnings.

Stdlib `logging`, configured to write human-readable text to stderr. The MCP
host (Claude Code, Codex) typically surfaces stderr in a debug pane, so
operators see lifecycle events and warnings when they need to investigate.

Distinct from `audit.py` (compliance, JSONL, on disk forever) and
`activity.py` (debugging, JSONL, rotation-friendly): diagnostics is
about the *server* itself rather than the operations it performs.

Configured once per process (call `setup_logging()` early in `serve`).
Modules emit via `logging.getLogger(__name__)` which inherits from the
`google_ads_mcp` logger tree — no per-module setup needed.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

_LOGGER_ROOT = "google_ads_mcp"


def setup_logging(level: LogLevel = "INFO") -> None:
    """Configure the `google_ads_mcp` logger tree for stderr output.

    Idempotent — repeated calls replace the handler instead of stacking.
    Doesn't touch the root logger or third-party loggers (the SDK's
    `google.ads.googleads` logger is silenced separately in `ads/client.py`
    so its per-request chatter doesn't leak through here).
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )

    logger = logging.getLogger(_LOGGER_ROOT)
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    logger.addHandler(handler)
    logger.setLevel(level)
    # Don't double-log via the root logger; keep our messages contained
    # so they don't interleave with anything else writing to stderr.
    logger.propagate = False
