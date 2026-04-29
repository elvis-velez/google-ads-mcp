# google-ads-mcp

A Model Context Protocol server for the Google Ads API. Lets you manage Google Ads campaigns from inside [Claude Code](https://claude.com/claude-code), [Codex](https://github.com/openai/codex), or any MCP-compatible client — with safe, two-phase mutations, hard guardrails, and an append-only audit log.

> Status: alpha (v0.0.1). All structural code paths are in place; full end-to-end acceptance against a real account requires Basic Access on your dev token (1–3 business days from Google).

## Why a custom server

Google ships an [official MCP](https://github.com/googleads/google-ads-mcp), but it's read-only by design — three tools, all reads. This one adds the writes you actually need to manage campaigns (pause, budgets, bids, negatives) with safety strong enough that an LLM can't accidentally set CPC to $1M.

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
| `pause_campaign(customer_id, campaign_id)` | 1 | Preview pausing a campaign. |
| `enable_campaign(customer_id, campaign_id)` | 1 | Preview enabling a campaign. |
| `set_campaign_budget(customer_id, budget_id, daily_amount_usd)` | 1 | Preview a daily budget change. USD → micros internally. |
| `add_negative_keyword(customer_id, scope, ref_id, text, match_type)` | 1 | Preview adding a campaign- or ad-group-level negative. |
| `account_summary(customer_id, date_range)` | 1 | Pre-baked GAQL: per-campaign performance, sorted by spend. |
| `ping()` | — | Connectivity check. Returns `"pong"`. |

Plus two resources:
- `gads-account://accessible` — customer IDs the credentials can operate on.
- `gads-schema://{resource_type}` — selectable / filterable / sortable fields per resource.

## Safety model

Every write is two-phase by default:

1. The LLM calls `mutate(...)` (or any Layer-1 outcome tool). The server runs guardrails, calls the API with `validate_only=true`, renders a per-operation diff, and stores the operations under a UUID `mutate_id` (15-minute TTL).
2. The LLM (or the human reviewing the diff) calls `apply(mutate_id)` to commit. Re-applying the same id returns the cached result and does **not** re-call the API.

Hard guardrails (server-enforced):

- **Customer-ID allowlist** — refuses operations on accounts the credentials can't access.
- **Batch size cap** — max 100 operations per call (not overridable; keeps diffs reviewable).
- **CPC cap** — default $50; per-account override via `~/.config/google-ads-mcp/limits.yaml`. Override per-op via `force_override=true`.
- **Daily budget cap** — default $1000; same override paths as CPC.

Per-account threshold overrides (`~/.config/google-ads-mcp/limits.yaml`):

```yaml
defaults:
  cpc_max_micros: 50000000

per_account:
  "1234567890":
    cpc_max_micros: 200000000   # legal/finance vertical
```

Audit log: every successful `apply` writes a JSONL line to `~/.local/share/google-ads-mcp/audit.log` (mode `0600`). Schema:

```json
{"timestamp":"...","mutate_id":"...","customer_id":"...","operations":[...],"resource_names":[...]}
```

## Configuration

Settings load with this precedence: env vars (prefixed `GOOGLE_ADS_MCP_`) > compiled-in defaults.

| Setting | Default | Notes |
|---|---|---|
| `credentials_path` | `~/.config/google-ads-mcp/credentials.yaml` | XDG-aware. |
| `audit_log_path` | `~/.local/share/google-ads-mcp/audit.log` | XDG-aware. |
| `limits_path` | `~/.config/google-ads-mcp/limits.yaml` | Optional file. |
| `gaql_max_rows` | `1000` | GAQL row cap. |
| `gaql_max_response_bytes` | `256000` | Approximate; caps total response size returned to the LLM. |
| `cpc_max_micros` | `50000000` ($50) | Default CPC cap. |
| `budget_max_daily_micros` | `1000000000` ($1000) | Default daily budget cap. |
| `mutate_max_ops_per_call` | `100` | Batch size cap. |
| `mutate_id_ttl_seconds` | `900` (15 min) | TTL for previewed mutates. |
| `log_level` | `INFO` | Server-level logging. |

## Development

```sh
git clone <this-repo>
cd google-ads-mcp
uv sync --group dev
uv run pytest             # 74 unit tests
uv run ruff check .
uv run pyright src tests  # strict mode
```

The test suite runs without credentials: anything above the `ads/` SDK boundary is testable with mocked stubs. Integration tests against Google's test-account environment are deferred until they're worth the maintenance cost.

## License

MIT. See `LICENSE`.
