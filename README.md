# google-ads-mcp

An MCP server for managing Google Ads accounts. Reads via GAQL, writes via two-phase preview/apply, full v24 API coverage in ~19 tools.

> Status: alpha (v0.0.1). End-to-end use against a real account requires Basic Access on your dev token (1–3 business days from Google).

## Features

- **GAQL reads** with row + byte caps that keep responses LLM-friendly.
- **Two-phase writes.** Every mutation runs `validate_only=true` first, returns a per-operation diff and a 15-minute `mutate_id`. `apply(mutate_id)` commits; re-applying is a no-op.
- **Customer-ID allowlist.** Server refuses calls for accounts the credentials can't access. Allowlist is built from `ListAccessibleCustomers` plus sub-accounts under your manager.
- **Append-only audit log.** JSONL at `~/.local/share/google-ads-mcp/audit.log` (mode `0600`). One line per state-changing attempt, including failures and idempotent replays.
- **Layer-1 outcome tools** for the common workflows: pause/enable campaigns, ad groups, keywords; set keyword bids; set campaign budgets; add negatives; apply Google's recommendations; generate keyword ideas.
- **Layer-2 generic dispatchers** cover the entire v24 surface. `mutate` covers all 64 services that fit the unified `MutateOperation` proto. `call_read_rpc` and `call_mutate_rpc` cover the long-tail RPCs (recommendation dismiss, experiment lifecycle, conversion uploads, audience insights, MCC management, link services, etc.).
- **Async-job lifecycles.** `batch_job` and `offline_user_data_job` cover BatchJob and Customer Match / Store Sales lifecycles via single dispatcher tools.
- **Discovery via MCP resources** — `gads-schema://`, `gads-rpc-catalog://`, `gads-rpc-schema://`. Loaded on demand, no ambient cost.
- **MCP tool annotations** — every tool declares `readOnlyHint`, `destructiveHint`, `idempotentHint` so clients render confirmations correctly.
- **Local-only OAuth.** Credentials at `~/.config/google-ads-mcp/credentials.yaml` (mode `0600`). No SaaS, no per-seat anything.

## Install

Install from source (PyPI publishing is on the roadmap; not available yet):

```sh
git clone https://github.com/ball2jh/google-ads-mcp.git
cd google-ads-mcp
uv sync
uv run google-ads-mcp init      # interactive setup
uv run google-ads-mcp validate  # re-validate without redoing OAuth
```

## First-run setup

`google-ads-mcp init` walks you through:

1. **OAuth client** — create a Google Cloud project, enable the Google Ads API, create a "Desktop app" OAuth client. ~5 minutes in the Cloud Console.
2. **Developer token** — apply at [apicenter](https://ads.google.com/aw/apicenter). Approval takes 1–3 business days. Test-only tokens work immediately for [Google Ads test accounts](https://developers.google.com/google-ads/api/docs/best-practices/test-accounts).
3. **Manager (MCC) account ID** — only if your dev token belongs to a manager and you want to operate on its sub-accounts. Dashes are stripped automatically.
4. **OAuth consent** — opens a browser, runs the loopback flow on a free localhost port, captures a refresh token.

Credentials persist to `~/.config/google-ads-mcp/credentials.yaml` (mode `0600`).

If validation fails after credentials are saved (e.g., your Cloud project hasn't enabled the Google Ads API yet), **don't re-run `init`** — that burns another OAuth refresh token. Fix the underlying issue, then run:

```sh
uv run google-ads-mcp validate
```

## Wire it into your MCP client

Point your client at the cloned checkout. Replace `/path/to/google-ads-mcp` with the absolute path to wherever you cloned this repo.

### Claude Code

```sh
claude mcp add google-ads -- uv run --directory /path/to/google-ads-mcp google-ads-mcp
```

### Codex

In `~/.codex/config.toml`:

```toml
[mcp_servers.google-ads]
command = "uv"
args = ["run", "--directory", "/path/to/google-ads-mcp", "google-ads-mcp"]
```

## Tool surface

| Tool | Layer | Description |
|---|---|---|
| `gaql(customer_id, query)` | 2 | Run any GAQL `SELECT`. Capped to keep responses LLM-context-friendly. |
| `mutate(customer_id, operations)` | 2 | Generic write via `GoogleAdsService.Mutate` (64 services). Returns a previewable `mutate_id`. |
| `call_read_rpc(customer_id, service, method, params)` | 2 | Generic read RPC for the long tail — keyword ideas, reach forecasts, audience insights, benchmarks, suggestions, list_invoices, etc. |
| `call_mutate_rpc(customer_id, service, method, params)` | 2 | Generic mutating RPC — recommendation apply/dismiss, experiment lifecycle, MCC management, conversion uploads, etc. |
| `apply(mutate_id)` | 2 | Commit a previewed mutate (operations or RPC). Idempotent. |
| `pause_campaign` / `enable_campaign(customer_id, campaign_id)` | 1 | Preview pausing/enabling a campaign. |
| `pause_ad_group` / `enable_ad_group(customer_id, ad_group_id)` | 1 | Granular pause/enable below the campaign level. |
| `pause_keyword` / `enable_keyword(customer_id, criterion_resource_name)` | 1 | Pause/enable a single ad-group criterion. |
| `set_keyword_bid(customer_id, criterion_resource_name, cpc_usd)` | 1 | Update a keyword's max CPC. USD → micros internally. |
| `set_campaign_budget(customer_id, budget_id, daily_amount_usd)` | 1 | Preview a daily budget change. USD → micros internally. |
| `add_negative_keyword(customer_id, scope, ref_id, text, match_type)` | 1 | Preview adding a campaign- or ad-group-level negative. |
| `apply_recommendation(customer_id, recommendation_resource_name)` | 1 | Apply one Google Ads recommendation. One-shot — Google has already validated it. |
| `generate_keyword_ideas(customer_id, seed_type, ...)` | 1 | SEM keyword research. Returns Google's keyword-idea expansions with avg searches, competition, suggested bids. |
| `batch_job(customer_id, action, ...)` | 1 | Async batch lifecycle: `create` → `add_operations` → `run` → `status` → `results`. |
| `offline_user_data_job(customer_id, action, ...)` | 1 | Customer Match / Store Sales upload lifecycle: `create` → `add_operations` → `run` → `status`. |
| `ping()` | — | Connectivity check. Returns `"pong"`. |

Resources:
- `gads-account://accessible` — customer IDs the credentials can operate on.
- `gads-schema://{resource_type}` — selectable / filterable / sortable fields per GAQL resource.
- `gads-rpc-catalog://` — every public RPC across the v24 SDK with `read_only` / `supports_validate_only` hints.
- `gads-rpc-schema://{service}/{method}` — per-method request proto fields.

## Architecture

Three layers behind a single SDK boundary. **Layer 1** has ~13 outcome-shaped tools that wrap **Layer 2**'s five generic escape hatches (`gaql`, `mutate`, `call_read_rpc`, `call_mutate_rpc`, `apply`). The `ads/` package is the only place that imports `google.ads.googleads.*`. Schema, account, and RPC discovery are MCP resources, not tools — they don't count against ambient context. Total tools stay constant as Google adds services.

Full structural plan: see `plan.md`.

## Safety model

The server enforces *invariants*, not your account's *policies*:

1. **Two-phase writes.** `mutate(...)` (or any Layer-1 write tool) calls the API with `validate_only=true`, renders a per-operation diff, stores under a `mutate_id` with a 15-minute TTL. `apply(mutate_id)` commits. Re-applying the same id returns the cached result and does **not** re-call the API.
2. **Customer-ID allowlist** built from real OAuth grants. Hallucinated IDs get rejected.
3. **Append-only audit log** of every attempt — success, validation failure, API error, expired re-apply, idempotent replay.
4. **MCP `ToolAnnotations`** on every tool so clients render confirmation prompts correctly.

The server deliberately doesn't enforce CPC caps, budget caps, or batch-size caps — those depend on your vertical (insurance routinely bids $200; ecommerce hits diminishing returns at $5). The two-phase preview is the actual safety mechanism: the new value is in the diff before commit, where the LLM and the human can both see it.

## Observability

Three logs, each with a different audience:

| Log | Path | Format | What it records |
|---|---|---|---|
| **Audit** | `~/.local/share/google-ads-mcp/audit.log` | JSONL, mode `0600` | Every state-changing attempt. The forensic answer to "did the LLM do X?" |
| **Activity** | `~/.local/share/google-ads-mcp/activity.log` | JSONL | Every tool/resource call: name, args summary, duration_ms, outcome. Reads included. |
| **Diagnostics** | stderr | text | Server lifecycle and operator-facing warnings. |

Audit and activity are write-once-per-line (POSIX append is atomic up to 4 KB). The diagnostic log is configurable via `log_level`; audit and activity are always on.

**Audit schema** (one JSON object per line):

```json
{
  "timestamp": "2026-04-28T18:30:15.123456+00:00",
  "phase":     "preview" | "apply",
  "outcome":   "ok" | "guardrail_rejection" | "validation_failed"
               | "api_error" | "expired" | "not_found" | "cached_replay",
  "mutate_id": "...",
  "customer_id": "1234567890",
  "payload_kind": "operations" | "rpc_call" | null,
  "operations": [{...}] | null,                                          // payload_kind=operations
  "rpc_call":   {"service":"...", "method":"...", "params":{...}} | null,  // payload_kind=rpc_call
  "result":     {"resource_names": [...]} | null,
  "error":      {"type": "...", "message": "...", "request_id": "..."} | null
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

Env vars prefixed `GOOGLE_ADS_MCP_` override compiled-in defaults.

| Setting | Default | Notes |
|---|---|---|
| `credentials_path` | `~/.config/google-ads-mcp/credentials.yaml` | XDG-aware. |
| `audit_log_path` | `~/.local/share/google-ads-mcp/audit.log` | XDG-aware. |
| `activity_log_path` | `~/.local/share/google-ads-mcp/activity.log` | XDG-aware. |
| `gaql_max_rows` | `1000` | GAQL row cap. |
| `gaql_max_response_bytes` | `256000` | Approximate response-size cap returned to the LLM. |
| `mutate_id_ttl_seconds` | `900` (15 min) | TTL for previewed mutates. |
| `log_level` | `INFO` | Diagnostic stderr level. |

## Development

```sh
git clone <this-repo>
cd google-ads-mcp
uv sync --group dev
uv run pytest             # 100 unit tests
uv run ruff check .
uv run pyright src tests  # strict mode
```

The test suite runs without credentials: anything above the `ads/` SDK boundary is testable with mocked stubs.

## License

MIT. See `LICENSE`.
