"""Allow `python -m google_ads_mcp` to invoke the CLI."""

from google_ads_mcp.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
