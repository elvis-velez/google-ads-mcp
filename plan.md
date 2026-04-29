# google-ads-mcp — Original Brain Dump

> **Status: superseded.** This is the original brain-dump from before scoping decisions
> were made. The current source of truth for the implemented architecture is the
> **README** (tool surface, safety, observability, configuration) and the structural
> plan kept in the contributor's `~/.claude/plans/`. Differences between this doc
> and what's actually shipping are deliberate, not regressions.
>
> Kept in the repo because the goals, motivation, and original Layer-1 intuitions are
> still useful context for future contributors.

## Why this exists

A Model Context Protocol server for the Google Ads API, designed around progressive
disclosure rather than tool-per-endpoint, so it stays usable as the API grows
(currently v24, ~110 services).

## Goals

1. **Manage Google Ads campaigns from Claude Code / Codex** without leaving the editor.
2. **Don't blow the context window** — total ambient tool definitions stay bounded by
   workflow categories, not by API surface size.
3. **Safe by default** — every write defaults to `validate_only=true` and requires
   an explicit `apply` step. No "the LLM set CPC to $1M" failure modes.
4. **Future-proof** — surviving v25, v26, etc. requires not coupling tool surface to
   API surface.

## Non-goals (still true in v1)

- UI / dashboard — Claude Code is the UI.
- Cross-platform (Meta/LinkedIn/TikTok) — Google Ads only.
- Automated bidding logic — humans in the loop on every change.
- Hosting it as a public service — runs locally, per-user creds.

## What changed from this doc to what shipped

**Layered architecture stayed.** Three layers behind a single SDK boundary, ambient
tool count bounded — exactly the original idea. See README "Architecture" for the
shipped layout.

**Layer 2 grew from 3 tools to 5.** Added `call_read_rpc` and `call_mutate_rpc` so
the long tail of Google Ads RPCs that don't fit `GoogleAdsService.Mutate` (recommendation
apply/dismiss, experiment lifecycle, conversion uploads, audience insights, reach
planning, etc. — ~40 services) is reachable without minting one Layer-1 tool per RPC.
Discovery via `gads-rpc-catalog://` and `gads-rpc-schema://` resources.

**Layer 3 (Code Mode) deferred to v2.** Sandbox story isn't worth the complexity for
v1; the two generic RPC escape hatches plus GAQL cover what Code Mode would have done
for the obvious cases. Re-evaluate if usage shows novel-script needs.

**Layer 1 stayed minimal.** ~13 outcome tools (pause/enable, set bid, set budget,
add negative, apply recommendation, generate keyword ideas, account summary, plus
`batch_job` and `offline_user_data_job` lifecycle dispatchers). Nothing else gets
promoted to Layer 1 unless evidence shows it's a daily LLM workflow.

**Hard guardrails (CPC cap / budget cap / batch-size cap) removed.** They're operator
policy, not server invariants — the right number depends on the vertical (insurance
routinely bids $200; ecommerce stops at $5). The two-phase preview is the actual
safety mechanism: the new bid is in the diff before commit. Server still enforces the
real invariants (customer-ID allowlist, append-only audit log).

**Original list of Layer-1 tools dropped or never built:**
- `create_rsa(headlines, descriptions)` — never built. Reachable via `mutate(operations)`
  for now; promote to Layer 1 if real workflows demand the typed shape.
- `account_summary` — built then removed. Replaceable by `gaql` + `gads-schema://` once
  the LLM has the schema, and not enough delta over GAQL to justify the slot.
- `list_accounts` — became a Resource (`gads-account://accessible`), not a tool.

## Setup prerequisites

These can't be code'd around — they're Google's process:

1. **Manager (MCC) account** — required to get a developer token. Free.
2. **Google Cloud project** — for OAuth client. Free.
3. **Developer token application** — submit at https://ads.google.com/aw/apicenter.
   **1–3 business days** for basic access approval. Test-only tokens work
   immediately for Google Ads test accounts.
4. **OAuth consent + refresh token** — `google-ads-mcp init` walks the loopback flow.

## Phase plan (delivered)

- ✅ **Phase 0 — Scaffolding.** Repo, `pyproject.toml`, license, CI.
- ✅ **Phase 1 — `gaql` + schema + accounts + auth.** Working against Google's test env.
- ✅ **Phase 2 — Generic mutate + safety.** Two-phase apply, audit log, allowlist.
- ✅ **Phase 3 — Outcome tools.** Layer-1 starter set: pause/enable for campaign /
  ad_group / keyword, set bid, set budget, add negative.
- ✅ **Phase 4 — Distribution.** PyPI metadata in place; install snippet docs.
- ✅ **Full coverage extension.** `call_read_rpc` / `call_mutate_rpc`, discovery
  resources, async-job dispatchers, `apply_recommendation`, `generate_keyword_ideas`.
- ⏳ **Code Mode (originally Phase 5).** Deferred to v2, behind real sandboxing.

## Open questions (resolved or pinned to README)

1. **Repo scope** — Google-only, named `google-ads-mcp`. Decision held.
2. **Code Mode runtime** — punted to v2.
3. **Multi-tenant credentials** — per-user, runs locally. Hosted-mode swaps the
   `CredentialsProvider` impl, no tool changes.
4. **Test account strategy** — Google's documented test env first; SNS / live MCC
   for integration testing later.
5. **Distribution** — PyPI for the `uvx google-ads-mcp` install path.

## References

- [Anthropic — Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [Cloudflare — Code Mode](https://blog.cloudflare.com/code-mode-mcp/)
- [Google Ads API v24 reference](https://developers.google.com/google-ads/api/reference/rpc)
- [Google's official google-ads-mcp (read-only, our reference for what they consider safe)](https://github.com/googleads/google-ads-mcp)
- [google-ads-python SDK](https://github.com/googleads/google-ads-python)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
