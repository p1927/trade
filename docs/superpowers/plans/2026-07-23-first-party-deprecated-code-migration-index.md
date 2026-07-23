# First-Party Deprecated / Legacy / Migration Audit

**Date:** 2026-07-23  
**Status:** Audit complete — see [proposed changes plan](./2026-07-23-first-party-deprecated-migration-proposed-changes.md) for design-aligned implementation  
**Rule:** [`.cursor/rules/no-defer-migrate-forward.mdc`](../../.cursor/rules/no-defer-migrate-forward.mdc)

## Goal

Inventory every deprecated, retired, shim, compat, and in-progress migration surface in **our** first-party code — excluding vendor submodules — with **git provenance** so we only track items we introduced or intentionally marked for removal.

## Methodology

1. **Search** first-party paths for high-signal patterns: `deprecated`, `legacy`, `migrate`, `obsolete`, `backward compat`, `re-export`, `will be removed`, `auto_paper`, `cli._legacy`, `HUB_NEWS_LEGACY`, etc.
2. **Attribute** each hit with `git blame` on the matching line.
3. **Include** only when introducing author is **ours**:
   - Primary: `Mishra, Pratyush <99pratyush@gmail.com>` (223 commits in `integrations/trade_integrations/`)
   - Also counted as ours: Trade-specific commit subjects on our fork (autonomous, hub-news, nautilus-bridge, etc.)
4. **Exclude** hits attributed to upstream Vibe Trading / TradingAgents authors (e.g. Haozhe Wu, Yijia-Xiao, HUANG Cheng) unless we added a separate deprecation marker in a later **our** commit.
5. **Submodule note:** `vibetrading/` is a git submodule — blame runs inside `vibetrading/` for agent/frontend paths; `integrations/` blame runs in the parent repo.

### Scope IN

| Path | Git repo |
|------|----------|
| `integrations/trade_integrations/` | Trade (parent) |
| `integrations/nautilus_openalgo_bridge/` | Trade (parent) |
| `vibetrading/agent/` | vibetrading submodule |
| `vibetrading/frontend/src/` | vibetrading submodule |
| `scripts/`, `tests/`, `stack/` | Trade (parent) |

### Scope OUT

`tradingagents/`, `openalgo/`, `nautilus_trader/`, `ed-alpha/`, `node_modules/`

---

## Executive summary

| Metric | Count |
|--------|-------|
| Deprecation-pattern line hits scanned | 289 |
| **Included (OURS)** | **90** lines across **41 files** |
| Excluded (UPSTREAM in our tree) | 199 lines |
| HTTP 410 retired routes (first-party) | 0 |
| `auto_paper` module on disk | **Removed** (zero refs outside deleted tree) |

**Top introducing commits (ours):**

| Commit | Subject | Items |
|--------|---------|-------|
| `b79326040cc1` | refactor(autonomous): remove auto_paper; migrate to autonomous_agents | auto_paper retirement, scheduler cleanup, legacy outcomes |
| `b7e9554afeb8` | Add index prediction pipeline, Nautilus bridge, and autonomous agents | poll loop fallback, bridge wiring, factor_cascade facade |
| `ec88e69d38ce` | feat(hub-news): events store, migrations, and dedup pipeline | news SSOT migration |
| `171bd64d0873` | feat(execution): OpenAlgo trading port and MarketContext paper gates | alpaca profile scope, trading_place_order comment |
| `c19e83e58a59` | refactor(autonomous): remove auto_paper stack; wire agent.action SSE | channel legacy session maps, obsolete scheduler in routes |

---

## Completed migration (verify + commit)

### auto_paper → autonomous_agents

| Item | Status | Introducing commit |
|------|--------|-------------------|
| `integrations/trade_integrations/auto_paper/` (18 modules) | **Deleted from disk** | Removed in `b79326040cc1` |
| `_autopaper_shims.py` | Deleted | Same refactor |
| `scripts/run_auto_paper_trading.py`, `run_paper_trading_agent.py` | Deleted | Same refactor |
| Remaining refs in live code | **None** (grep clean) | — |
| [`scheduler_cleanup.py`](../../integrations/trade_integrations/autonomous_agents/scheduler_cleanup.py) `OBSOLETE_SCHEDULER_JOB_IDS` | Delete-only hygiene for old cron ids | `b79326040cc1` |

**Gate:** Commit pending deletions; `pytest tests/test_scheduler_cleanup.py -q`

---

## Full inventory — OURS only

Legend: **Cat** = category | **Target** = replacement authority | **Diff** = easy / medium / hard

### 1. Retired modules & cleanup

| ID | File | Cat | Target | Commit | Diff |
|----|------|-----|--------|--------|------|
| R-1 | [`scheduler_cleanup.py`](../../integrations/trade_integrations/autonomous_agents/scheduler_cleanup.py) | retired | Per-agent `{id}-watch/research/...` jobs | `b79326040cc1` | easy |
| R-2 | [`tests/test_scheduler_cleanup.py`](../../tests/test_scheduler_cleanup.py) | test anchor | — | `b79326040cc1` | easy |
| R-3 | [`autonomous_routes.py`](../../vibetrading/agent/src/api/autonomous_routes.py) `remove_obsolete_scheduler_jobs` | retired hook | Same as R-1 | `c19e83e58a59` | easy |
| R-4 | [`watch.py`](../../integrations/trade_integrations/autonomous_agents/watch.py) L270 legacy watch removed | retired guard | Nautilus bridge required | `92f9da69c128` | BY DESIGN |
| R-5 | [`teardown.py`](../../integrations/trade_integrations/autonomous_agents/teardown.py) `legacy_alpaca_path_removed` | tombstone | OpenAlgo US plugin | `24b377274898` | BY DESIGN |

### 2. Dead aliases & facades (zero or near-zero callers)

| ID | File | Cat | Target | Commit | Diff |
|----|------|-----|--------|--------|------|
| D-1 | [`handoff.py:248`](../../integrations/nautilus_openalgo_bridge/handoff.py) `enqueue_intent` | shim | `submit_intent` | `b7e9554afeb8` | easy |
| D-2 | [`poll_loop.py:284`](../../integrations/nautilus_openalgo_bridge/runtime/poll_loop.py) `run_once_alpaca` | dead | `run_once` | `d3c8c67aa510` | easy |
| D-3 | [`plan_approval.py:69`](../../integrations/trade_integrations/autonomous_agents/plan_approval.py) `activate_agent_watch_after_approval` | shim | `activate_agent_watch` | `92f9da69c128` | easy |
| D-4 | [`hub_wiki/compile.py:131`](../../integrations/trade_integrations/dataflows/hub_wiki/compile.py) `render_event_page` | shim | `render_event_source` | `b1633ea48f9e` | easy |
| D-5 | [`news_llm_batch_dedup.py`](../../integrations/trade_integrations/dataflows/index_research/news_llm_batch_dedup.py) | deprecated module | `news_llm_story_pipeline` | `0b94bed7d8c2` | easy |
| D-6 | [`factor_cascade.py`](../../integrations/trade_integrations/dataflows/index_research/factor_cascade.py) | re-export | `cascade` package | `b7e9554afeb8` | easy |
| D-7 | [`ResearchArtifactSidebar.tsx`](../../vibetrading/frontend/src/components/research/ResearchArtifactSidebar.tsx) | @deprecated | `ContextDrawer` | `422a53011414` | easy |
| D-8 | [`api.ts:670`](../../vibetrading/frontend/src/lib/api.ts) `getOrchestratorSession` | dead API | `createDraftAutonomousAgent` | `a2a4537f747b` | easy |
| D-9 | [`nse_browser/session.py:477`](../../integrations/trade_integrations/nse_browser/session.py) `click_download_links` | deprecated | `trigger_csv_download` | `0d6f138e8dc8` | easy |

### 3. Active migrations (dual paths — migrate then delete)

| ID | File | Cat | Target | Commit | Diff |
|----|------|-----|--------|--------|------|
| M-1 | [`news_migrations.py`](../../integrations/trade_integrations/hub_storage/news_migrations.py) + [`migrate_hub_news_records_once.py`](../../scripts/migrate_hub_news_records_once.py) | **data cutover** | `events.parquet` SSOT | `ec88e69d38ce`, `7321e7ffad92` | hard |
| M-2 | [`news_events_store.py`](../../integrations/trade_integrations/hub_storage/news_events_store.py) legacy record adapters | compat shim | Native event shape | `ec88e69d38ce` | hard |
| M-3 | [`verified_news_store.py`](../../integrations/trade_integrations/hub_storage/verified_news_store.py) `iter_legacy_verified_records` | migration-only | Events queries | `ec88e69d38ce` | medium |
| M-4 | [`news_staging_store.py`](../../integrations/trade_integrations/hub_storage/news_staging_store.py) `HUB_NEWS_LEGACY_INGEST` | flag-gated | Entity pipeline + staging | `174a8af2a6c1` | medium |
| M-5 | [`store.py:395`](../../integrations/trade_integrations/autonomous_agents/store.py) `backfill_orphan_orchestrator_session` | migrate | Draft-agent model (no `orchestrator.json`) | `24b377274898` | medium |
| M-6 | [`plan_approval.py:236`](../../integrations/trade_integrations/autonomous_agents/plan_approval.py) `normalize_legacy_plan_approval` | lazy backfill | New plan-approval widget | `92f9da69c128` | medium |
| M-7 | [`watch_registry/store.py`](../../integrations/trade_integrations/watch_registry/store.py) `migrate_agent_watch_spec_to_registry` | cutover helper | Registry-only watches | `c7baf0fad411` | medium |
| M-8 | [`outcome_ledger.py`](../../integrations/trade_integrations/autonomous_agents/outcome_ledger.py) `legacy_outcomes.parquet` | self-migrate | `outcomes.parquet` | `b79326040cc1` | easy |
| M-9 | [`hub_wiki/bootstrap.py`](../../integrations/trade_integrations/dataflows/hub_wiki/bootstrap.py) `migrate_legacy_sources_layout` | one-shot | `llm-wiki/raw/sources/news/` | `b1633ea48f9e` | medium |
| M-10 | [`history_ingest.py:83`](../../integrations/trade_integrations/dataflows/index_research/history_ingest.py) legacy deriv cols | data fix | `flow_derivatives_daily` ownership | `13208484ef01` | medium |

### 4. API & frontend legacy surfaces

| ID | File | Cat | Target | Commit | Diff |
|----|------|-----|--------|--------|------|
| A-1 | [`trade_routes.py:3029`](../../vibetrading/agent/src/api/trade_routes.py) POST `/run/stream` | legacy SSE | `POST /run/start` + `GET /run/{id}/stream` | `c1cc62c96765` | medium |
| A-2 | [`trade_routes.py:2514`](../../vibetrading/agent/src/api/trade_routes.py) POST `/refresh/stream` | legacy SSE | `GET /refresh/{job_id}/stream` | `d51b5e89ae6b` | medium |
| A-3 | [`api.ts:460,1051`](../../vibetrading/frontend/src/lib/api.ts) legacy stream POSTs | legacy client | Job-scoped GET streams | `a2a4537f747b`, `d51b5e89ae6b` | medium |
| A-4 | [`autonomous_routes.py:244`](../../vibetrading/agent/src/api/autonomous_routes.py) POST `/orchestrator/session` | legacy duplicate | **`POST /drafts` already canonical** — remove alias only | (ours via submodule) | easy |

**Note:** [`usePredictionRunCoordinator.ts`](../../vibetrading/frontend/src/hooks/usePredictionRunCoordinator.ts) already prefers job API; legacy POST is 404/405 fallback only. External-predictions refresh still uses A-2/A-3 directly.

### 5. Execution & connector (ours)

| ID | File | Cat | Target | Commit | Diff |
|----|------|-----|--------|--------|------|
| E-1 | [`default_profile.py`](../../integrations/trade_integrations/execution/default_profile.py) `alpaca-paper-sdk` | fallback profile | OpenAlgo-only when keys present | `171bd64d0873` | medium |
| E-2 | [`prompt_fragments.py:8`](../../integrations/trade_integrations/execution/prompt_fragments.py) `trading_place_order` comment | legacy tool path | OpenAlgo MCP / bridge intent | `171bd64d0873` | medium |
| E-3 | [`openalgo_client.py`](../../integrations/nautilus_openalgo_bridge/openalgo_client.py) importlib load | shim | Direct import from `execution.openalgo_client` | `b7e9554afeb8` | medium |
| E-4 | [`dataflows/alpaca.py`](../../integrations/trade_integrations/dataflows/alpaca.py) module scope | scoped legacy | Research/backfill only; not agent execution | `171bd64d0873` | BY DESIGN |

### 6. Nautilus bridge ops fallback (ours)

| ID | File | Cat | Target | Commit | Diff |
|----|------|-----|--------|--------|------|
| N-1 | [`poll_loop.py`](../../integrations/nautilus_openalgo_bridge/runtime/poll_loop.py) `--legacy-poll` | ops fallback | Default TradingNode | `b7e9554afeb8` | medium |
| N-2 | [`run_watch_node.py`](../../integrations/nautilus_openalgo_bridge/runtime/run_watch_node.py) CLI | flag | Same as N-1 | `b7e9554afeb8` | medium |
| N-3 | [`run_nautilus_watch.sh`](../../scripts/run_nautilus_watch.sh) | script | Same as N-1 | `b7e9554afeb8` | medium |
| N-4 | [`verify_autonomous_integration.py`](../../scripts/verify_autonomous_integration.py) rejects legacy poll | migration gate | Live node required | `b7e9554afeb8` | BY DESIGN |

### 7. CLI & stack boot (mixed provenance)

| ID | File | Cat | Target | Commit | Diff |
|----|------|-----|--------|--------|------|
| C-1 | [`stack_lib.sh`](../../scripts/stack_lib.sh) `cli._legacy serve` | legacy boot | `cli.main serve` or dedicated entry | `b4a32a12c800` | hard |
| C-2 | [`cli/__init__.py`](../../vibetrading/agent/cli/__init__.py) lazy `_legacy` re-exports | structural shim | Modular `cli/commands/*` | `c87d20a69d9c` | hard |

### 8. Channel session data (ours — auto_paper refactor)

| ID | File | Cat | Target | Commit | Diff |
|----|------|-----|--------|--------|------|
| CH-1 | [`channels/runtime.py`](../../vibetrading/agent/src/channels/runtime.py) `_load_legacy_session_map` | data migrate | Current session store | `c19e83e58a59` | medium |
| CH-2 | [`channels/mochat.py`](../../vibetrading/agent/src/channels/mochat.py) `_load_legacy_session_cursors` | data migrate | Current cursor store | `c19e83e58a59` | medium |

### 9. Minor compat shims (ours — keep or delete after caller grep)

| ID | File | Commit |
|----|------|--------|
| S-1 | [`news_distillation.py:130`](../../integrations/trade_integrations/dataflows/index_research/news_distillation.py) | `eed5019bb65f` |
| S-2 | [`prediction_data_requirements.py:154`](../../integrations/trade_integrations/dataflows/index_research/prediction_data_requirements.py) | `f294a85d10f6` |
| S-3 | [`http/gateway.py:14`](../../integrations/trade_integrations/http/gateway.py) re-export comment | `a5863666b185` |
| S-4 | [`setup_vibe.py:215`](../../scripts/setup_vibe.py) `sync_skill` alias | `4037b6a24d7f` |
| S-5 | [`minimax_agent.py:51`](../../integrations/trade_integrations/nse_browser/minimax_agent.py) max_tokens map | `0b94bed7d8c2` |

---

## Excluded — upstream in our tree (do not migrate in this program)

These exist under first-party paths but **git blame attributes introduction to upstream authors**. Track separately if we fork-merge from vibetrading upstream.

| File | Symbol / marker | Author | Commit |
|------|-----------------|--------|--------|
| `vibetrading/agent/cli/_legacy.py` | Entire 5k-line CLI | Haozhe Wu et al. | upstream |
| `vibetrading/agent/mcp_server.py` | `--transport sse` deprecated | HUANG Cheng | `a6fe3fc6a2d9` |
| `vibetrading/agent/src/tools/trading_connector_tool.py` | `trading_place_order` tool | Haozhe Wu | `a3d6dddee33c` |
| `vibetrading/agent/src/api/settings_routes.py` | `LEGACY_ENV_PATH` | Haozhe Wu | `984b362472f4` |
| `integrations/trade_integrations/agents/sentiment_analyst.py` | `create_social_media_analyst` deprecated | Yijia-Xiao | `0fcf13624e8a` |
| ~199 additional line hits | Various `legacy`/`compat` in vibe agent tests, providers, channels | Mixed upstream | — |

**Policy:** When upstream removes these, merge from submodule. When **we** need behavior change, add a thin Trade wrapper in `integrations/trade_integrations/` rather than editing upstream files long-term.

---

## Migration phase map

**Design-aligned proposal:** [2026-07-23-first-party-deprecated-migration-proposed-changes.md](./2026-07-23-first-party-deprecated-migration-proposed-changes.md)

| Phase | Type | Depends | Items | Gate |
|-------|------|---------|-------|------|
| **0** | cleanup | — | D-1–D-9 (not S-2, S-5) | Scoped pytest exit 0 |
| **1** | migrate | — | Commit auto_paper deletion (R-*) | No `auto_paper` refs |
| **2** | migrate | 0 | A-1–A-4 (SSE + remove orchestrator/session duplicate) | Job-scoped streams only |
| **3** | migrate | — | M-1–M-4 (hub news SSOT) | `legacy_remaining=0` |
| **4** | migrate | watch registry | M-5–M-7, M-9 | No lazy backfill on agent load |
| **5** | refactor | [unified-openalgo plan](./2026-07-17-unified-openalgo-data-channel.md) | E-3, `dataflows/openalgo.py` callers | Channel + `openalgo.*` imports |
| **6** | refactor | 2 | C-1, C-2 — **`cli.main serve` in stack** (approved) | `trade up` + `/live` 200 |
| **7** | cleanup | 4–5 | E-1 autonomous-only Alpaca block, E-2 | `aa_*` cannot resolve Alpaca SDK profile |
| **8** | cleanup | 4 | N-1–N-3 — **remove legacy poll now** (approved) | No `--legacy-poll` in scripts/CLI |

**Order:** 0 → 1 → 2 → 3 → 4 → 8 → 7 → 5 → 6

**Execution:** Sequential phases per [`multi-phase-plan-convergence.mdc`](../../.cursor/rules/multi-phase-plan-convergence.mdc). One subplan file per phase before implementation.

---

## Verification commands

```bash
# Regenerate provenance audit (ours vs upstream)
python3 scripts/audit_deprecated_provenance.py   # TODO: optional script from this audit

# Deprecation grep regression (first-party only)
rg -n "deprecated|legacy|shim|backward compat|auto_paper|cli\._legacy|HUB_NEWS_LEGACY" \
  integrations/trade_integrations integrations/nautilus_openalgo_bridge \
  vibetrading/agent vibetrading/frontend/src scripts tests stack \
  --glob '!**/node_modules/**'

# Phase gates
pytest tests/test_scheduler_cleanup.py tests/test_news_migrations.py \
  tests/test_nautilus_poll_dispatch.py -q --timeout=120
```

---

## Related plans

| Plan | Relationship |
|------|--------------|
| [2026-07-23-openalgo-market-authority-index.md](./2026-07-23-openalgo-market-authority-index.md) | Phases 0–4 met; E-1/E-4 scoped |
| [2026-07-17-unified-openalgo-data-channel.md](./2026-07-17-unified-openalgo-data-channel.md) | Phase 5 caller migration |
| [2026-07-22-unified-watch-registry.md](./2026-07-22-unified-watch-registry.md) | M-7 cutover helper removal |
| [2026-07-16-autonomous-remaining-phases.md](./2026-07-16-autonomous-remaining-phases.md) | Autonomous UX; A-4 orchestrator route |

---

## Audit artifact

Raw git-blame scan output (289 lines, 90 OURS / 199 UPSTREAM): generated in session 2026-07-23 via ripgrep + `git blame --porcelain` over first-party paths.
