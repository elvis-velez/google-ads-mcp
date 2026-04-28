# google-ads-mcp — Plan

A Model Context Protocol server for the Google Ads API, designed around progressive disclosure and Code Mode rather than tool-per-endpoint, so it stays usable as the API grows (currently v24, ~hundreds of services).

---

## Goals

1. **Manage Google Ads campaigns from Claude Code / Codex** without leaving the editor.
2. **Don't blow the context window** — total ambient tool definitions stay under ~5k tokens regardless of how many services Google adds.
3. **Safe by default** — every write defaults to `validate_only=true` and requires an explicit `apply` step. No "the LLM set CPC to $1M" failure modes.
4. **Multi-account aware** — works across Nettarion's MCC and any client subaccounts (currently Sell Now Solutions; built so Michael's other clients can plug in without code changes).
5. **Future-proof** — surviving v25, v26, etc. requires not coupling tool surface to API surface.

## Non-goals (for v1)

- UI / dashboard — Claude Code is the UI.
- Cross-platform (Meta/LinkedIn/TikTok) — Google Ads only. Add later if useful.
- Automated bidding logic — humans in the loop on every change.
- Hosting it as a public service — runs locally, per-user creds.

---

## Architecture

Three-layer design, drawn from Anthropic's [Code Execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) and Cloudflare's [Code Mode](https://blog.cloudflare.com/code-mode-mcp/):

### Layer 1 — Outcome tools (~10 tools, always loaded)

Workflow-shaped, not API-shaped. Cover ~95% of daily use, each wraps Layer 2 with safer defaults.

| Tool | Wraps | Notes |
|---|---|---|
| `pause_campaign(customer_id, campaign_id)` | `CampaignService.MutateCampaigns` | Sets status=PAUSED |
| `enable_campaign(customer_id, campaign_id)` | `CampaignService.MutateCampaigns` | Sets status=ENABLED |
| `set_campaign_budget(customer_id, budget_id, daily_amount_usd)` | `CampaignBudgetService` | Converts USD → micros internally |
| `add_negative_keyword(customer_id, scope, text, match_type)` | `CampaignCriterionService` or `AdGroupCriterionService` | scope = "campaign"\|"ad_group"\|"shared_set" |
| `set_keyword_bid(customer_id, criterion_id, cpc_usd)` | `AdGroupCriterionService` | USD → micros |
| `pause_keyword(customer_id, criterion_id)` | `AdGroupCriterionService` |
| `create_rsa(customer_id, ad_group_id, headlines[], descriptions[])` | `AdGroupAdService` | Validates 3+ headlines, 2+ descriptions |
| `apply_recommendation(customer_id, recommendation_id)` | `RecommendationService` |
| `list_accounts()` | `CustomerService.ListAccessibleCustomers` |
| `account_summary(customer_id, date_range)` | `GoogleAdsService.SearchStream` | Pre-baked GAQL for quick health check |

### Layer 2 — Generic escape hatches (3 tools, always loaded)

For everything Layer 1 doesn't cover.

| Tool | Purpose |
|---|---|
| `gaql(customer_id, query)` | Run any GAQL `SELECT`. Covers 100% of reads. |
| `describe(resource_name)` | Returns proto schema for any resource on demand. Cheap lookup, expensive only when called. |
| `mutate(customer_id, operations[], validate_only=true)` | Generic write. `apply` step required to actually commit (returns mutate_id, then `apply(mutate_id)` runs it). |

### Layer 3 — Code Mode (1 tool, optional, deferred)

| Tool | Purpose |
|---|---|
| `run_python(code)` | Sandboxed Python with `google-ads` SDK preloaded + `customer_id` in scope. The 5% escape hatch. |

Defaults to off; user opts in per-session. When enabled, Layer 1 + Layer 2 still preferred — Layer 3 is for novel scripts the LLM writes once.

### Total ambient cost

~14 tool definitions, est. 3–5k tokens. Constant regardless of API surface.

---

## Safety model

**Mutate flow is two-phase by default:**

1. Model calls `mutate(...)` or any Layer 1 write tool → server runs with `validate_only=true`, returns:
   - Validation result (would it succeed?)
   - Diff preview (what would change, in human-readable form)
   - A `mutate_id` (UUID, in-memory, 5-min TTL)
2. Model calls `apply(mutate_id)` → server re-runs with `validate_only=false`.

**Allow-skipping** for low-risk operations via env var (e.g., `ALLOW_DIRECT_MUTATE=pause_campaign,enable_campaign`). Default: nothing skips.

**Audit log:** every `apply` writes JSONL to `~/.config/google-ads-mcp/audit.log` with timestamp, customer_id, operation, payload, result.

**Hard guardrails (server-enforced, not opt-out):**
- Reject CPC > $50 unless explicit override flag in payload.
- Reject daily budget > $1000 unless explicit override.
- Reject any operation touching > 100 entities in one call (force batching, makes diffs reviewable).

---

## Tech choices

- **Language:** Python 3.13. Reason: official `google-ads` SDK is most mature in Python; proto types come for free.
- **MCP SDK:** `mcp` (official Anthropic Python SDK).
- **Transport:** stdio (local-only, per-user). No HTTP server in v1.
- **Package manager:** `uv` (fast, lockfile, no venv ceremony).
- **Auth:** OAuth2 refresh token flow, creds in `~/.config/google-ads-mcp/credentials.yaml`. One-time setup script.
- **Testing:** `pytest` + `responses` for HTTP mocks + a test MCC subaccount for integration tests.

---

## Setup prerequisites (one-time, on user side)

These can't be code'd around — they're Google's process:

1. **Manager (MCC) account** — required to get a developer token. Free.
2. **Google Cloud project** — for OAuth client. Free.
3. **Developer token application** — submit at https://ads.google.com/aw/apicenter. **1–3 business days for basic access** approval.
4. **OAuth consent + refresh token** — one-time `auth.py` script we'll ship.

We can scaffold/test against the [Google Ads API test accounts](https://developers.google.com/google-ads/api/docs/best-practices/test-accounts) while waiting for token approval — those work without it.

---

## Phases

### Phase 0 — Scaffolding (this session, today)

- [x] Create repo, plan.md, public on GitHub
- [ ] `pyproject.toml`, `uv.lock`, basic project structure
- [ ] README with quick-start (placeholder, finalize at end)
- [ ] `.gitignore`, license (MIT)
- [ ] CI: GitHub Actions for lint + type-check (no Google Ads creds needed)

### Phase 1 — Core: gaql + describe + auth (week 1)

- [ ] OAuth flow + credential storage
- [ ] `gaql` tool, working against test account
- [ ] `describe` tool, pulled from `GoogleAdsFieldService`
- [ ] `list_accounts` tool
- [ ] Manual smoke test with Claude Code

**Acceptance:** Can ask Claude "show me last 7 days CPA by campaign for account X" and get a real answer.

### Phase 2 — Generic mutate + safety (week 2)

- [ ] `mutate` tool with `validate_only` default
- [ ] `apply` tool with mutate_id store
- [ ] Audit logging
- [ ] Hard guardrails (CPC/budget/batch-size limits)

**Acceptance:** Can pause a campaign via the generic `mutate` path with a visible diff preview and explicit apply step.

### Phase 3 — Outcome tools (week 3)

- [ ] All Layer 1 tools above, each wrapping Layer 2
- [ ] Schema validation per tool (catch obvious bad input before round-trip)
- [ ] Account summary GAQL templates

**Acceptance:** Can run a typical optimization session end-to-end (review → pause underperformers → add negatives → apply) using only Layer 1 tools.

### Phase 4 — Code Mode (week 4, optional)

- [ ] `run_python` tool with sandboxed subprocess
- [ ] SDK preloaded, customer_id injected, validate_only enforced
- [ ] Output capture + size limits

**Acceptance:** Can ask "find every keyword whose CTR dropped >50% this month vs last and propose a list to pause" and get back a draft mutate plan.

### Phase 5 — Polish

- [ ] Real README with examples + screenshots
- [ ] Setup script (`google-ads-mcp init`)
- [ ] Publish to PyPI? (decide later)
- [ ] Document MCP install snippet for Claude Code + Codex

---

## Open questions

1. **Repo scope:** just Google Ads, or "nettarion-ads-mcp" with Meta/LinkedIn slots reserved? Recommendation: keep it Google-only and named `google-ads-mcp` — easier to extract reusable patterns later than to prematurely abstract.
2. **Code Mode runtime:** Python subprocess (simplest) vs. [Vercel Sandbox](https://vercel.com/docs/vercel-sandbox) (Firecracker isolation, better safety, requires network). Recommendation: subprocess for v1; Sandbox if we ever hosted-mode this.
3. **Multi-tenant credentials:** one refresh token per local user vs. per-customer. Recommendation: per-user, since you're the operator.
4. **Test account strategy:** create a dedicated SNS test account or use Google's documented test environment? Recommendation: Google's test env first (free, no real spend risk).
5. **Distribution:** GitHub-only, PyPI, or both? PyPI gives `uvx google-ads-mcp` install convenience.

---

## References

- [Anthropic — Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [Cloudflare — Code Mode](https://blog.cloudflare.com/code-mode-mcp/)
- [Google Ads API v24 reference](https://developers.google.com/google-ads/api/reference/rpc)
- [Google's official google-ads-mcp (read-only, our reference for what they consider safe)](https://github.com/googleads/google-ads-mcp)
- [google-ads-python SDK](https://github.com/googleads/google-ads-python)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
