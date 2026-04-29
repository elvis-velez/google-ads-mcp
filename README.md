# google-ads-mcp

A Model Context Protocol server for the Google Ads API. Lets you manage Google Ads campaigns from inside [Claude Code](https://claude.com/claude-code), [Codex](https://github.com/openai/codex), or any MCP-compatible client — with safe, two-phase mutations and an append-only audit log.

> Status: alpha (v0.0.1). All structural code paths are in place; full end-to-end acceptance against a real account requires Basic Access on your dev token (1–3 business days from Google).

## Why a custom server

Google ships an [official MCP](https://github.com/googleads/google-ads-mcp), but it's read-only by design — three tools, all reads. This one adds the writes you actually need to manage campaigns (pause, budgets, bids, negatives) with the two-phase preview-then-apply contract that makes those writes safe for an LLM to drive.

## Architecture in one paragraph

Three layers behind a single SDK boundary. **Layer 1** has ~5 outcome-shaped tools (`pause_campaign`, `set_campaign_budget`, …) that wrap **Layer 2**'s two generic escape hatches: `gaql` for all reads, `mutate` for all writes. Below them, the `ads/` package is the only place that imports `google.ads.googleads.*`. Schema lookup and account discovery are MCP **resources** (`gads-schema://`, `gads-account://`), not tools — they don't count against the ambient context-token budget. Total registered tools stay constant regardless of how many services Google adds to the API.

Full structural plan: see `plan.md`.

## Install

Once published to PyPI:

```sh
uvx google-ads-mcp init      # interactive setup
uvx google-ads-mcp validate  # re-validate without redoing OAuth
```

Pre-PyPI (from a checkout):

```sh
git clone <this-repo>
cd google-ads-mcp
uv sync
uv run google-ads-mcp init
```

## First-run setup

`google-ads-mcp init` walks you through:

1. **OAuth client** — create a Google Cloud project, enable the Google Ads API, create a "Desktop app" OAuth client. ~5 minutes in the Cloud Console.
2. **Developer token** — apply at [apicenter](https://ads.google.com/aw/apicenter). Approval takes 1–3 business days. (Test-only tokens work immediately for Google Ads test accounts; see [Google's docs](https://developers.google.com/google-ads/api/docs/best-practices/test-accounts).)
3. **Manager (MCC) account ID** — optional, only if your dev token belongs to a manager and you want to operate on its sub-accounts. Dashes in the ID are stripped automatically.
4. **OAuth consent** — opens a browser, runs the loopback flow on a free localhost port, captures a refresh token.

Credentials are persisted to `~/.config/google-ads-mcp/credentials.yaml` with mode `0600`.

If validation fails after credentials are saved (e.g., your Cloud project hasn't enabled the Google Ads API yet), **don't re-run `init`** — that would burn another OAuth refresh token. Fix the underlying issue, then run:

```sh
google-ads-mcp validate
```

## Wiring into MCP clients

### Claude Code

```sh
claude mcp add google-ads -- uvx google-ads-mcp
```

Or, while developing locally:

```sh
claude mcp add google-ads -- uv run --directory /path/to/google-ads-mcp google-ads-mcp
```

### Codex

In `~/.codex/config.toml`:

```toml
[mcp_servers.google-ads]
command = "uvx"
args = ["google-ads-mcp"]
```

## Tool surface

| Tool | Layer | Description |
|---|---|---|
| `gaql(customer_id, query)` | 2 | Run any GAQL `SELECT`. Capped to keep responses LLM-context-friendly. |
| `mutate(customer_id, operations)` | 2 | Generic write. Validates against the API and returns a previewable `mutate_id`. |
| `apply(mutate_id)` | 2 | Commit a previewed mutate. Idempotent — re-applying returns the cached result. |
| `pause_campaign` / `enable_campaign(customer_id, campaign_id)` | 1 | Preview pausing/enabling a campaign. |
| `pause_ad_group` / `enable_ad_group(customer_id, ad_group_id)` | 1 | Granular pause/enable below the campaign level. |
| `pause_keyword` / `enable_keyword(customer_id, criterion_resource_name)` | 1 | Pause/enable a single ad-group criterion. The most common tactical optimization. |
| `set_keyword_bid(customer_id, criterion_resource_name, cpc_usd)` | 1 | Update a keyword's max CPC. USD → micros internally. |
| `set_campaign_budget(customer_id, budget_id, daily_amount_usd)` | 1 | Preview a daily budget change. USD → micros internally. |
| `add_negative_keyword(customer_id, scope, ref_id, text, match_type)` | 1 | Preview adding a campaign- or ad-group-level negative. |
| `apply_recommendation(customer_id, recommendation_resource_name)` | 1 | Apply one Google Ads recommendation. One-shot (no validate/apply two-phase) since Google has already validated it. |
| `account_summary(customer_id, date_range)` | 1 | Pre-baked GAQL: per-campaign performance, sorted by spend. |
| `ping()` | — | Connectivity check. Returns `"pong"`. |

Plus two resources:
- `gads-account://accessible` — customer IDs the credentials can operate on.
- `gads-schema://{resource_type}` — selectable / filterable / sortable fields per resource.

## Safety model

The MCP server enforces server-side *invariants*, not your account's *policies*. The four things it guarantees:

1. **Two-phase writes.** The LLM calls `mutate(...)` (or any Layer-1 outcome tool). The server validates against the API with `validate_only=true`, renders a per-operation diff, and stores the operations under a UUID `mutate_id` (15-minute TTL). Nothing has happened on Google's side yet. The LLM (or the human reviewing the diff) calls `apply(mutate_id)` to commit. Re-applying the same id returns the cached result and does **not** re-call the API.
2. **Customer-ID allowlist.** Refuses operations on accounts the credentials can't access. Real defense against an LLM hallucinating a customer_id.
3. **Append-only audit log.** Every state-changing attempt — success, validation failure, API error, expired re-apply — gets a JSONL line at `~/.local/share/google-ads-mcp/audit.log` (mode `0600`). One file, one grep, the whole forensic story.
4. **MCP `ToolAnnotations`.** Each tool declares whether it's `readOnlyHint`, `destructiveHint`, `idempotentHint`, etc. so MCP clients (Claude Code, Codex) can render confirmation prompts intelligently.

What the server *deliberately doesn't* enforce: CPC caps, daily-budget caps, batch-size caps, or any other business rule about what bid is "too high" for your account. Those depend entirely on your vertical (insurance routinely bids $200; ecommerce hits diminishing returns at $5) — they're the operator's call, not the MCP server's. The two-phase preview is the actual safety mechanism: the new bid is in the diff before commit, where the LLM and the human can both see it.

## Observability

Three distinct logs, each with a different audience:

| Log | Path | Format | What it records |
|---|---|---|---|
| **Audit** | `~/.local/share/google-ads-mcp/audit.log` | JSONL, mode `0600` | Every state-changing *attempt* — success, guardrail rejection, validation failure, API error, expired/cached re-apply. The forensic answer to "did the LLM do X on this account?" |
| **Activity** | `~/.local/share/google-ads-mcp/activity.log` | JSONL | Every tool/resource call: name, args summary, duration_ms, outcome. Reads included. The debugging answer to "what was the LLM doing yesterday?" |
| **Diagnostics** | stderr | text | Server lifecycle (start/stop, config paths) and operator-facing warnings. The MCP host (Claude Code, Codex) shows this in its debug pane. |

Audit and activity are write-once-per-line (POSIX append is atomic up to 4KB). The diagnostic log is configurable via the `log_level` setting; audit and activity are always on.

**Audit schema** (one JSON object per line):

```json
{
  "timestamp": "2026-04-28T18:30:15.123456+00:00",
  "phase":     "preview" | "apply",
  "outcome":   "ok" | "guardrail_rejection" | "validation_failed"
               | "api_error" | "expired" | "not_found" | "cached_replay",
  "mutate_id": "...",
  "customer_id": "1234567890",
  "operations": [{...}],
  "result":    {"resource_names": [...]} | null,
  "error":     {"type": "...", "message": "...", "request_id": "..."} | null
}
```

**Activity schema**:

```json
{
  "timestamp":    "...",
  "kind":         "tool" | "resource",
  "name":         "pause_campaign",
  "args_summary": {...},
  "duration_ms":  123,
  "outcome":      "ok" | "error",
  "error":        {"type": "...", "message": "..."} | null
}
```

## Configuration

Settings load with this precedence: env vars (prefixed `GOOGLE_ADS_MCP_`) > compiled-in defaults.

| Setting | Default | Notes |
|---|---|---|
| `credentials_path` | `~/.config/google-ads-mcp/credentials.yaml` | XDG-aware. |
| `audit_log_path` | `~/.local/share/google-ads-mcp/audit.log` | XDG-aware. |
| `activity_log_path` | `~/.local/share/google-ads-mcp/activity.log` | XDG-aware. |
| `gaql_max_rows` | `1000` | GAQL row cap (protects the LLM's context window). |
| `gaql_max_response_bytes` | `256000` | Approximate response-size cap returned to the LLM. |
| `mutate_id_ttl_seconds` | `900` (15 min) | TTL for previewed mutates. |
| `log_level` | `INFO` | Diagnostic stderr level. |

## Development

```sh
git clone <this-repo>
cd google-ads-mcp
uv sync --group dev
uv run pytest             # 65 unit tests
uv run ruff check .
uv run pyright src tests  # strict mode
```

The test suite runs without credentials: anything above the `ads/` SDK boundary is testable with mocked stubs. Integration tests against Google's test-account environment are deferred until they're worth the maintenance cost.

## License

MIT. See `LICENSE`.
