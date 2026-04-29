"""CLI entry point.

Subcommands land in later phases (`init`, plus anything else operators need).
For Phase 0, the binary just runs the server — which is what `claude mcp add
google-ads-mcp -- uvx google-ads-mcp` and equivalents expect.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from google_ads_mcp import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="google-ads-mcp",
        description="MCP server for the Google Ads API.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"google-ads-mcp {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser(
        "serve",
        help="Run the MCP server over stdio (default when no subcommand given).",
    )
    serve.set_defaults(func=_cmd_serve)

    init = subparsers.add_parser(
        "init",
        help="Interactive first-run setup: OAuth, credentials.yaml, validation.",
    )
    init.set_defaults(func=_cmd_init)

    return parser


def _cmd_serve(_args: argparse.Namespace) -> int:
    from google_ads_mcp.server import run

    run()
    return 0


def _cmd_init(_args: argparse.Namespace) -> int:
    from google_ads_mcp.auth.init_cmd import run_init

    return run_init()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # No subcommand → default to `serve`. Typical MCP client invocation runs
    # the binary with no arguments and expects stdio.
    if not getattr(args, "command", None):
        return _cmd_serve(args)

    return args.func(args)
