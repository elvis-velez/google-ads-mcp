# google-ads-mcp

Model Context Protocol server for the Google Ads API. Lets you manage Google Ads campaigns from inside Claude Code or Codex with safe, two-phase mutations.

> Status: early development. See `plan.md` and the structural plan for direction.

## Install (preview)

```sh
uvx google-ads-mcp
```

Full install + `init` walkthrough lands in Phase 4.

## Why

Google's [official MCP](https://github.com/googleads/google-ads-mcp) is read-only by design. This one adds writes — pause/enable, budget, bids, negatives, RSAs — with safety strong enough that an LLM can't accidentally set CPC to $1M.

## Architecture

Three-layer design, kept under ~5k ambient tokens regardless of API growth:

- **Layer 1** — outcome tools (`pause_campaign`, `set_campaign_budget`, …)
- **Layer 2** — generic escape hatches (`gaql`, `mutate`, `apply`)
- **`ads/` boundary** — the only module that imports `google.ads.googleads.*`

Schema lookup and account discovery are exposed as MCP **Resources**, not tools, so they don't count against the ambient budget.

See `plan.md` for the original brain-dump and the structural plan for the current source of truth.

## License

MIT
