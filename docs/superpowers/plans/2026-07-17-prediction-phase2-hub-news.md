# Prediction Phase 2 — Hub News SSOT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Last inventory:** 2026-07-23 — entity SSOT + resolver dedup shipped in `7321e7f`; see [hub-news-resolver-dedup index](./2026-07-23-hub-news-resolver-dedup-index.md).

**Goal:** Make the Prediction tab’s headlines and news impact **read from hub SSOT only** on normal Run and live poll; **ingest fresh news into hub only when the user checks “Refresh all 50 constituents”**; eliminate duplicate live-fetch paths that cause rate limits and empty News Impact panels.

**Architecture:** One rule — **OpenAlgo = market data; BSE + hub = events/news; tiered APIs (Tapetide, Alpha Vantage) = index-level only, never Nifty-50 batch.** Constituent batch news fetches SearXNG → `news_hub_bridge.ingest_*` → staging queue (+ entity worker distillation). Normal analysis reuses cached index snapshot; live poll is macro + spot only.

**Tech Stack:** Python 3.12+, `news_hub_bridge`, `news_staging_store`, `news_entity_worker`, `news_impact_engine`, FastAPI `/index-prediction/*`, React Prediction tab.

**Related plans:**
- `docs/superpowers/specs/2026-07-17-hub-distilled-news-entity-design.md`
- `docs/superpowers/plans/2026-07-17-hub-distilled-news-entity.md` (Tasks 1–10 **Done**)
- `docs/superpowers/plans/2026-07-23-hub-news-resolver-dedup-index.md` (resolver **Done**)
- `docs/superpowers/plans/2026-07-16-prediction-news-impact-panel.md` (UI contract)

## Global Constraints

- All news reads/writes through `trade_integrations.dataflows.news_hub_bridge`.
- **Live poll** must not call `batch_constituent_research` or news ingest.
- **Normal Run** (`refresh_constituents=false`) must not live-fetch news or re-research constituents.
- **“Refresh all 50”** is the only user path that may cold-fetch per-symbol news and write to hub.
- Index-level panel Refresh may use tiered APIs for **NIFTY only** — not 50-symbol batch.
- Nifty-50 batch news: **SearXNG only** (`fetch_policy.NIFTY50_BATCH_NEWS_SOURCES`).
- After backend deploy: `trade reload app`.

---

## Repo status (2026-07-17)

### Branches

All feature branches were already merged into `origin/main` before this session. No branch merges were required.

| Branch | Status |
|--------|--------|
| `feat/create-agent-session-lifecycle` | Merged (PR #1); delete locally when convenient |
| `feat/nifty-index-research-pipeline` | Merged; delete locally when convenient |
| `feat/unified-openalgo-data-channel` | Merged; delete locally when convenient |

### Local commits (not yet pushed)

```
3410d84 chore(submodules): bump vibetrading and openalgo for prediction hub work
53c3d76 test(index-research): cover flow cache merge and NSE browser parsers
91f7e32 chore(submodules): bump vibetrading for prediction UI and routes
e0a8604 docs: add hub news entity specs and prediction phase 2 plan
17cd482 chore(data): refresh NSE flow and sector index parquet snapshots
345eb59 feat(stack): searxng TLS wrapper, trade CLI lifecycle, and env helpers
ae73282 feat(index-research): alpha bridge, factor catalog, and news scenarios
52c5c85 feat(hub-news): staging queue, entity worker, and bridge ingest path
a9b47ac fix(index-prediction): cached constituents for normal run and light poll
c070e1d feat(company-research): gate tiered APIs during Nifty-50 batch
```

**Next ops step:** `git push origin main` (+ push `vibetrading` and `openalgo` submodules) then `trade reload app`.

**Not committed (intentionally):** `log/` runtime artifacts, `stack/searxng/certs/` (gitignored TLS material).

---

## Inventory — what exists vs what does not

### ✅ Shipped (Phase 1 + partial Phase 2/3)

| Capability | Location | Notes |
|------------|----------|-------|
| Batch tiered API gate | `fetch_policy.py`, company research sources, `news_aggregator` | Tapetide/AV skipped in Nifty-50 batch |
| SearXNG-only batch news fetch | `company_research/sources/news.py` | No peer fan-out in batch |
| Cached constituent snapshot | `constituent_snapshot.py` | `signals_from_cached_doc()` |
| Normal run skips batch research | `aggregator.py` | `refresh_constituents=false` → hub snapshot only; requires prior full run |
| Macro-only light poll | `light_refresh.py` | No `batch_constituent_research` on poll |
| Flow completeness cached-only gate | `data_completeness.py` | `enrich=refresh_constituents`, `allow_live_fetch=False` on normal run |
| Index news impact branch by refresh flag | `aggregator.py` | `refresh=True` → `refresh_news_impact(refresh_ingest=True)`; else `resolve_news_impact` |
| Hub staging queue | `hub_storage/news_staging_store.py` | `enqueue_raw_ref`, `staging_queue_stats` |
| Entity worker + distillation | `news_entity_worker.py`, `news_distillation.py`, `news_event_matching.py` | Match, LLM distill, merge into verified store |
| Bridge ingest → staging | `news_hub_bridge/_ingest.py` | When `HUB_NEWS_ENTITY_PIPELINE` enabled (default **on**) |
| Union read staging + verified | `news_hub_bridge.query_verified_news`, `union_headlines_with_staging` | |
| Material news watcher → hub | `monitor/news_watcher.py` | Index-level ingest on material headlines |
| News Impact empty-state UI | `NewsImpactPanel.tsx` | “No verified headlines…” copy |
| Live poll error surfacing | `useIndexPredictionLive.ts` | API message instead of generic `refresh_failed` |
| Hub news entity cron job | `index_jobs.py` | `JOB_TYPE_HUB_NEWS_ENTITY`, default `35 18 * * *` |
| Staging CLI | `scripts/process_hub_news_staging.py` | Manual drain |
| API staging endpoint | `trade_routes.py` | `process_staging_batch` for ops |
| Tests | `test_fetch_policy`, `test_index_light_refresh_pipeline_log`, `test_news_hub_bridge`, etc. | 13+ targeted tests pass |

### ⚠️ Partial — prediction panel contract (not entity/resolver)

| Gap | Current behavior | Target |
|-----|------------------|--------|
| `headlines_for_day` fallback | Still calls `collect_headlines_for_day` when hub empty on some paths | Return `hub_empty`; no live collect on normal run |
| News Impact GET auto-refresh | `refresh_news_impact(refresh_ingest=False)` when resolve empty | OK if ingest false; verify no hidden tiered fetch |
| Constituent news factors | From cached `company_research` doc, not hub union | Optional Phase 2b hub read |
| Normal run prerequisite | Requires prior refresh-all-50 for constituent signals | Surface clearly in UI |

### ❌ Remaining (Phase 2 tasks)

| Item | Status |
|------|--------|
| Hub-read-only guard tests | Not started |
| `hub_empty` status in API + panel | Not started |
| `constituent_news_as_of` on index doc | Not started |
| `.env.example` docs for index workers | Partial |

### ✅ Shipped (Phase 3 entity SSOT — 2026-07-23)

| Item | Status |
|------|--------|
| `news_events_store.py` / `news_event_models.py` | **Done** |
| `events.parquet` SSOT + `event_index.parquet` | **Done** — see resolver dedup index |
| Backfill `records.parquet` → events | **Done** — `ensure_hub_news_migrations()` |
| Unified resolver + club merge + post-upsert safety | **Done** — `news_resolver.py`, `news_event_clubbing.py`, `news_post_upsert_safety.py` |

### ❌ Remaining (Phase 2/3 UX — not blocking resolver)

| Item | Status |
|------|--------|
| News Impact UI timeline + references expandable | Not started |
| `hub_empty` status in API + panel | Not started |
| `constituent_news_as_of` on index doc | Not started |
| Hub-read-only guard tests (block live collect on normal run) | Not started |
| Debounced market-hours-only staging worker | Partial — `schedule_staging_processing` exists; no market-hours gate |
| `headlines_for_day` tiered fallback when hub empty | Partial — still live-collects on some paths |

---

## Actual runtime flows (post-commit)

### Normal Run — Refresh all 50 **OFF**

```
run_index_research(refresh_constituents=False)
  → load_index_research_json → signals_from_cached_doc   # NO batch_constituent_research
  → attach_constituent_momentum (cached OHLCV)
  → macro / predict / scenarios
  → resolve_news_impact(hydrate_from_hub=True)           # NO refresh_ingest
```

**Requires:** prior full run with Refresh all 50 checked at least once.

### Full Run — Refresh all 50 **ON**

```
run_index_research(refresh_constituents=True)
  → batch_constituent_research(refresh=True)
       → run_company_research × 50 (SearXNG news, no Tapetide/AV)
       → save company_research JSON
       → ingest headline rows → news_hub_bridge → staging  ✅
  → refresh_news_impact(refresh_ingest=True)              # NIFTY index tiered ingest OK
```

### Live poll

```
run_index_light_refresh()
  → signals_from_cached_doc(cached index doc)
  → macro + OpenAlgo spot
  → re-predict; replace pipeline_log with light_refresh log
```

### News Impact panel

```
GET /index-prediction/news-impact
  → resolve_news_impact (default)
  → if empty: refresh_news_impact(refresh_ingest=False)
Panel Refresh button → refresh_ingest=True (NIFTY index only)
```

---

## Prerequisite 0: Land Phase 1 — ✅ DONE

Committed in `c070e1d`, `a9b47ac`, and related tests. Push to remote and reload app before UI verification.

- [x] `fetch_policy.py` + tiered gating
- [x] `light_refresh.py` + `constituent_snapshot.py`
- [x] `batch_constituents.py` batch mode
- [x] `news.py` SearXNG batch path
- [x] Tests + live poll error surfacing (vibetrading submodule)

---

## Phase 2 — Remaining tasks (implement next)

### Task 1: Hub ingest on constituent refresh only — ✅ DONE

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/constituent_news_ingest.py` ✅
- Modify: `integrations/trade_integrations/dataflows/index_research/sources/batch_constituents.py` ✅
- Test: `tests/test_constituent_news_ingest.py` ✅

- [x] Write tests (skip when `refresh=False`, run when `refresh=True`)
- [x] Implement `headline_rows_from_company_doc` + `maybe_ingest_constituent_news`
- [x] Wire `_research_one` after `save_company_research` when `refresh=True`
- [ ] Verify `staging_queue_stats()` grows after refresh-all-50 run (manual E2E — Task 5)

---

### Task 2: Hub-read-only news impact — ✅ DONE

**Done:**
- [x] `resolve_news_impact` returns `status: hub_empty` when no hub rows (no live collect)
- [x] `build_news_impact_snapshot` returns `hub_empty` when `refresh_ingest=False` and empty
- [x] `headlines_for_day` skips `collect_headlines_for_day` unless `allow_live_collect=True`
- [x] API default load uses `resolve_news_impact` only (removed empty-path `refresh_news_impact`)
- [x] Tests block live collect on normal resolve paths

**Remaining:** none for Task 2 core scope

---

### Task 3: Constituent factor staleness (2b) — ❌ NOT STARTED

**Decision:** Keep `build_constituent_factors` on cached company research for Phase 2. Add visibility only.

- [ ] Pipeline log field `constituent_news_as_of` from last refresh timestamp
- [ ] Prediction UI hint: “Constituent news through {date} — run Refresh all 50 to update”

**Defer to Phase 3:** `build_constituent_factors_from_hub()` via `query_verified_news(ticker=symbol)`.

---

### Task 4: News Impact panel semantics — ✅ DONE

**Done:**
- [x] Empty-state copy mentions Refresh all 50 + Ingest new (index-only)
- [x] Handles `hub_empty` status from API
- [x] Shows staging `pending_count` when queued refs exist
- [x] OpenAPI/doc comment on news-impact route refresh semantics

---

### Task 5: E2E verification — ⚠️ PARTIAL

- [x] `pytest tests/test_constituent_news_ingest.py tests/test_news_impact_engine.py -q` (19 passed)
- [x] Push commits to `origin/main`
- [ ] `trade reload app` after deploy
- [ ] Run without Refresh all 50: no Tapetide/AV in logs
- [ ] Run with Refresh all 50: staging queue grows per symbol
- [ ] Live poll 3×: <25s, `light_refresh` log only

---

## Phase 3 — Hub distilled news entity (partial foundation shipped)

Foundation exists in `52c5c85` but full spec is incomplete.

| Spec item | Shipped? |
|-----------|----------|
| Staging queue | ✅ `news_staging_store.py` |
| Distillation worker | ✅ `news_entity_worker.py` |
| LLM distill contract | ✅ `news_distillation.py` |
| Union read | ✅ `union_headlines_with_staging` |
| Cron job registration | ✅ `JOB_TYPE_HUB_NEWS_ENTITY` |
| `news_events_store.py` | ❌ |
| `events.parquet` SSOT | ❌ — still `records.parquet` |
| Backfill migration | ❌ |
| UI timeline/references | ❌ |

Continue with `docs/superpowers/plans/2026-07-17-hub-distilled-news-entity.md` **after Phase 2 Task 1** (constituent hub ingest) is stable.

**Flag note:** `HUB_NEWS_ENTITY_PIPELINE` defaults to **on**. Consider setting default to **off** until Task 1 populates staging from batch refresh, or document that index-level ingest + watcher are the primary feeders today.

---

## Phase 4 — Prediction equation integration (unchanged — defer)

No coef edits without walk-forward OOS gate (+3 pp). Wire hub-tagged factors only after Phase 2–3 hub population is reliable.

---

## Phase 5 — Operations

- [ ] `git push origin main` (+ submodule pushes)
- [ ] Delete stale local `feat/*` branches
- [ ] Document env vars in `.env.example`
- [ ] Optional: nightly NIFTY-only `refresh_news_impact(refresh_ingest=True)` job (separate from user Run)

---

## Self-review (updated)

| Requirement | Status |
|-------------|--------|
| No tiered APIs in Nifty batch | ✅ Shipped |
| Poll never batch research | ✅ Shipped |
| Normal run no constituent re-research | ✅ Shipped (stricter than original plan) |
| Hub ingest on Refresh all 50 only | ✅ Task 1 |
| Normal run hub-read-only news impact | ✅ Task 2 |
| News Impact panel | ✅ Task 4 |
| Distilled entity SSOT | ⚠️ Phase 3 partial |

---

## Execution handoff

**Prerequisite 0 is complete locally.** Phase 2 reduces to **Tasks 1, 2 (remainder), 4 (remainder), 5**.

**Phase 2 core complete.** Optional Task 3 (staleness UI) and Task 5 manual E2E remain.

**Execution options:**
1. **Subagent-Driven (recommended)** — one task per subagent with review gate
2. **Inline Execution** — implement Task 1 in this session, then re-verify E2E
