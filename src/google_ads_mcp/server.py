"""FastMCP server entry point.

Builds the MCP server, registers tools / resources / prompts, and runs the
chosen transport. Phase 0 has only a `ping` tool to validate the wiring;
real tooling lands in later phases.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def build_server() -> FastMCP:
    """Construct and configure the MCP server.

    Centralised so tests can build a fresh server without running it, and so
    later phases have one place to wire in injected dependencies.
    """
    mcp = FastMCP("google-ads-mcp")

    @mcp.tool()
    def ping() -> str:  # pyright: ignore[reportUnusedFunction]
        """Returns 'pong'. Smoke test that the server is reachable."""
        return "pong"

    return mcp


def run() -> None:
    """Run the server over stdio. Default transport for local MCP clients."""
    server = build_server()
    server.run(transport="stdio")
