# Prediction Phase 2 — Hub News SSOT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Prediction tab’s headlines and news impact **read from hub SSOT only** on normal Run and live poll; **ingest fresh news into hub only when the user checks “Refresh all 50 constituents”**; eliminate duplicate live-fetch paths that cause rate limits and empty News Impact panels.

**Architecture:** One rule — **OpenAlgo = market data; BSE + hub = events/news; tiered APIs (Tapetide, Alpha Vantage) = index-level only, never Nifty-50 batch.** Constituent batch news fetches SearXNG (and optionally yfinance RSS) → `news_hub_bridge.ingest_*` → staging queue (+ optional distillation). Normal analysis loads cached company research + hub headlines; live poll never re-researches constituents or fetches news.

**Tech Stack:** Python 3.12+, `news_hub_bridge`, `news_staging_store`, `news_impact_engine`, `batch_constituents`, FastAPI `/index-prediction/*`, React Prediction tab.

**Related specs/plans:**
- Agreed design (conversation 2026-07-17): hub ingest on “Refresh all 50” only; poll macro+spot only
- `docs/superpowers/specs/2026-07-17-hub-distilled-news-entity-design.md` (Phase 3+ distillation)
- `docs/superpowers/plans/2026-07-17-hub-distilled-news-entity.md` (full entity pipeline — defer heavy LLM work to Phase 3)
- `docs/superpowers/plans/2026-07-16-prediction-news-impact-panel.md` (UI contract)

## Global Constraints

- All news reads/writes through `trade_integrations.dataflows.news_hub_bridge` — never import `verified_news_store` or `news_impact_engine` from UI/API/agents.
- **Live poll (`run_index_light_refresh`) must not call `batch_constituent_research` or any news ingest.**
- **Normal Run (`refresh_constituents=false`) must not live-fetch news** — read hub + cached `company_research/latest.json` only.
- **“Refresh all 50 constituents” (`refresh_constituents=true`)** is the only user-triggered path that may fetch per-symbol news and write to hub.
- Index-level `refresh_news_impact(refresh_ingest=True)` may still use tiered APIs for **NIFTY** ticker only — not for 50-symbol batch.
- Nifty-50 batch news backends: **SearXNG only** (`fetch_policy.NIFTY50_BATCH_NEWS_SOURCES`) — no Tapetide, no Alpha Vantage.
- Backward-compatible headline dict shape for `NewsImpactPanel` and existing tests.
- After backend deploy: `trade reload app` so `:8899` serves changes.

---

## Branch / merge status (2026-07-17)

| Branch | vs `origin/main` | GitHub PR | Action |
|--------|------------------|-----------|--------|
| `feat/create-agent-session-lifecycle` | 0 ahead, 79 behind | #1 MERGED | Safe to delete locally |
| `feat/nifty-index-research-pipeline` | 0 ahead, 108 behind | (direct merge) | Safe to delete locally |
| `feat/unified-openalgo-data-channel` | 0 ahead, 0 behind | (direct merge) | Safe to delete locally |

**Nothing remains to merge into `main`.** OpenAlgo channel, news scenarios, and agent lifecycle are already on `origin/main`.

**Gap:** ~61 files of **uncommitted local work** (Phase 1 + partial hub-news entity). That work is **not** on `main` until committed and pushed. Phase 2 must not start until Phase 1 is landed (see Prerequisite 0).

---

## File map (Phase 2)

| File | Responsibility |
|------|----------------|
| `company_research/fetch_policy.py` | Batch vs tiered API gate (Phase 1 — must land first) |
| `index_research/light_refresh.py` | Poll uses cached constituent snapshot only (Phase 1) |
| `index_research/constituent_snapshot.py` | Shared `signals_from_cached_doc()` (dedupe with light_refresh) |
| `index_research/sources/batch_constituents.py` | Pass `refresh` flag; optional hub ingest hook after cold research |
| `company_research/sources/news.py` | Batch: SearXNG fetch; on refresh-only: ingest rows to hub |
| `news_hub_bridge/_ingest.py` | `ingest_rows_to_hub` → staging queue (already local) |
| `index_research/news_impact_engine.py` | Disable live ingest on normal resolve; hub-read-only default |
| `index_research/constituent_factors.py` | Optional: news factors from hub read (Phase 2b) |
| `index_research/aggregator.py` | After full run: `sync_news_impact_to_index_doc` without tiered batch ingest |
| `monitor/news_watcher.py` | Index-level material news → hub ingest (keep); no 50-symbol fan-out |
| `vibetrading/agent/src/api/trade_routes.py` | Pass `refresh_constituents` through; document news-impact refresh semantics |
| `vibetrading/frontend/.../NewsImpactPanel.tsx` | Show empty-state when hub has no rows; no silent tiered fetch |
| `tests/test_index_batch_constituents.py` | Assert hub ingest called only when `refresh=True` |
| `tests/test_news_impact_engine.py` | Assert `resolve_news_impact` does not call aggregator when hub populated |

---

## Prerequisite 0: Land Phase 1 (blocking)

**Uncommitted work that must ship before Phase 2:**

1. `fetch_policy.py` + Tapetide/AV gating in `identity_in`, `calendar_in`, `fundamentals_in`, `peers_in`, `news_aggregator`
2. `light_refresh.py` → `_signals_from_cached_doc` / `constituent_snapshot.py` (no `batch_constituent_research` on poll)
3. `batch_constituents.py` → `set_nifty50_batch(True)`, `include_macro=False`
4. `news.py` batch SearXNG-only path
5. Tests: `test_fetch_policy.py`, updated `test_index_light_refresh_pipeline_log.py`, `test_index_batch_constituents.py`
6. `useIndexPredictionLive.ts` — surface API error message

**Suggested PR split (3 commits / PRs):**

| PR | Files | Message |
|----|-------|---------|
| A | `fetch_policy`, company research sources, news_aggregator | `feat(company-research): gate tiered APIs during Nifty-50 batch` |
| B | `light_refresh`, `constituent_snapshot`, tests | `fix(index-prediction): macro-only light refresh without constituent batch` |
| C | `news.py` batch SearXNG, `.env.example` docs | `feat(index-prediction): SearXNG-only batch news fetch` |

After merge: `trade reload app` + verify poll completes in <25s with reason `unchanged` or `macro_updated`.

---

## Phase 2 — Hub headlines for Prediction (implement next)

### Design summary

```
┌─────────────────────────────────────────────────────────────────┐
│ User: Run analysis (Refresh all 50 = OFF)                       │
│   batch_constituent_research(refresh=False)                     │
│     → load cached company_research (60m TTL)                    │
│     → NO live news fetch                                        │
│   news_impact: resolve from hub only (refresh_ingest=False)     │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ User: Run analysis (Refresh all 50 = ON)                        │
│   batch_constituent_research(refresh=True)                      │
│     → run_company_research per symbol (SearXNG news only)       │
│     → ingest headline rows → news_hub_bridge → staging          │
│   aggregator: sync_news_impact_to_index_doc(doc)                │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Live poll (every N seconds)                                     │
│   run_index_light_refresh()                                     │
│     → cached constituent_signals from index_research doc        │
│     → macro + OpenAlgo spot only                                │
│     → NEVER ingest news, NEVER batch_constituent_research       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ News Impact panel (separate GET)                                │
│   resolve_news_impact(hydrate_from_hub=True, refresh_ingest=False)│
│   User clicks Refresh → refresh_news_impact(refresh_ingest=True)  │
│     → tiered ingest for NIFTY index only (not 50 symbols)       │
└─────────────────────────────────────────────────────────────────┘
```

---

### Task 1: Hub ingest hook on constituent refresh only

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/constituent_news_ingest.py`
- Modify: `integrations/trade_integrations/dataflows/index_research/sources/batch_constituents.py`
- Modify: `integrations/trade_integrations/dataflows/company_research/sources/news.py`
- Test: `tests/test_constituent_news_ingest.py`

**Interfaces:**
- Consumes: `news_hub_bridge.ingest_rows_to_hub`, `news_hub_bridge.hub_ticker_for_symbol`, `fetch_policy.is_nifty50_batch()`
- Produces:
  - `ingest_company_news_to_hub(doc: CompanyResearchDoc, *, symbol: str) -> dict[str, int]`
  - Called from `_research_one` **only when** `refresh=True` and news stage has headlines

- [ ] **Step 1: Write the failing test**

```python
def test_ingest_skipped_when_not_refresh(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.index_research.constituent_news_ingest import (
        maybe_ingest_constituent_news,
    )

    calls = []
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.ingest_rows_to_hub",
        lambda *a, **k: calls.append(1) or {"ingested": 1},
    )
    doc = _fake_doc_with_news("RELIANCE", headlines=[{"title": "Reliance beats estimates"}])
    stats = maybe_ingest_constituent_news(doc, symbol="RELIANCE", refresh=False)
    assert stats == {"ingested": 0, "skipped": True}
    assert calls == []


def test_ingest_runs_when_refresh(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.index_research.constituent_news_ingest import (
        maybe_ingest_constituent_news,
    )

    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.ingest_rows_to_hub",
        lambda rows, *, ticker, **k: {"ingested": len(rows), "ticker": ticker},
    )
    doc = _fake_doc_with_news("RELIANCE", headlines=[{"title": "Reliance beats estimates"}])
    stats = maybe_ingest_constituent_news(doc, symbol="RELIANCE", refresh=True)
    assert stats["ingested"] == 1
```

- [ ] **Step 2: Run test — expect FAIL** (`pytest tests/test_constituent_news_ingest.py -v`)

- [ ] **Step 3: Implement `constituent_news_ingest.py`**

Extract headlines from `doc.stages` news block / `data.blocks[].headlines`; map to hub rows (`title`, `summary`, `url`, `published_at`, `source`); call `ingest_rows_to_hub(rows, ticker=hub_ticker_for_symbol(symbol))`.

Wire in `_research_one`: after `save_company_research`, if `refresh: maybe_ingest_constituent_news(doc, symbol=symbol, refresh=True)`.

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Commit** `feat(index-prediction): ingest constituent news to hub on refresh-all-50 only`

---

### Task 2: Hub-read-only news impact on normal Run

**Files:**
- Modify: `integrations/trade_integrations/dataflows/index_research/aggregator.py` (~line 350+ after prediction)
- Modify: `integrations/trade_integrations/dataflows/index_research/news_impact_engine.py` (`resolve_news_impact`, `headlines_for_day`)
- Modify: `integrations/trade_integrations/context/hub.py` (`load_index_research_json` hydrate path)
- Test: `tests/test_news_impact_engine.py` (extend)

**Interfaces:**
- Consumes: `news_hub_bridge.sync_news_impact_to_index_doc`, `resolve_news_impact(..., hydrate_from_hub=True)`
- Produces: `run_index_research` sets `doc.news_impact` from hub without calling `collect_headlines_for_day` when `refresh_constituents=False`

- [ ] **Step 1: Write failing test**

```python
def test_run_index_research_normal_does_not_collect_headlines(hub_tmp, monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_collect.collect_headlines_for_day",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("live collect forbidden")),
    )
    # mock batch to return cached signals quickly
    doc = run_index_research("NIFTY", refresh_constituents=False)
    assert doc.news_impact is not None
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Change `resolve_news_impact` default path**

When `hydrate_from_hub=True` and snapshot/items exist: return without `refresh_ingest`. When empty and `refresh_constituents=False`: return empty report with `status: "hub_empty"` — **do not** fall through to `collect_headlines_for_day`.

In `aggregator.run_index_research`: call `sync_news_impact_to_index_doc(doc)` with `refresh_ingest=refresh_constituents` (True only on full refresh).

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Commit** `fix(news-impact): hub-read-only on normal index run`

---

### Task 3: Remove duplicate interim batch news from constituent factors (optional 2b)

**Decision (recommended for Phase 2):** Keep `build_constituent_factors` reading news from **cached** `company_research` doc (populated on last refresh). Do **not** add live hub read per symbol on every Run — too slow. Document that constituent news factors are stale until user runs “Refresh all 50”.

**Files:**
- Modify: `integrations/trade_integrations/dataflows/index_research/constituent_factors.py` (docstring + cap news factors at 3)
- Modify: `docs/superpowers/plans/2026-07-17-prediction-phase2-hub-news.md` (this file — mark 2b deferred if skipped)

**Alternative 2b (Phase 3):** `build_constituent_factors_from_hub(symbol, lookahead_days)` reading `query_verified_news(ticker=symbol, since=...)`.

- [ ] **Step 1:** Add pipeline log field `constituent_news_as_of` on index doc from max hub `published_at` for top-10 weights
- [ ] **Step 2:** Surface in Prediction UI as “Constituent news through {date}”

---

### Task 4: News Impact panel empty-state + index-only refresh

**Files:**
- Modify: `vibetrading/frontend/src/components/prediction/NewsImpactPanel.tsx`
- Modify: `vibetrading/agent/src/api/trade_routes.py` (`get_index_prediction_news_impact`)

**Behavior:**
- Default load: `resolve_news_impact` only (no ingest)
- Empty hub: show “No verified headlines yet. Run analysis with **Refresh all 50 constituents** or click Refresh for index-level news.”
- Panel Refresh button: `refresh_ingest=True` for **NIFTY** only (existing endpoint — document in OpenAPI comment)

- [ ] **Step 1:** Add `hub_empty` status handling in panel
- [ ] **Step 2:** Manual verify: Run without refresh → panel shows empty-state; Run with refresh → headlines appear after ingest
- [ ] **Step 3:** Commit `feat(prediction-ui): news impact empty-state and refresh semantics`

---

### Task 5: End-to-end verification checklist

- [ ] `pytest tests/test_index_light_refresh_pipeline_log.py tests/test_constituent_news_ingest.py tests/test_news_impact_engine.py -q`
- [ ] `trade reload app`
- [ ] **Run without Refresh all 50:** completes without Tapetide/AV log lines; News Impact shows hub-only data
- [ ] **Run with Refresh all 50:** SearXNG ingest logs; hub staging queue depth increases (`news_hub_bridge.staging_queue_stats()`)
- [ ] **Live poll 3×:** each <25s; pipeline log stage `light_refresh`; no `constituents` progress lines
- [ ] **News Impact Refresh:** index-level ingest only; no 50-symbol parallel fetch

---

## Phase 3 — Hub distilled news entity (follow-on)

Execute `docs/superpowers/plans/2026-07-17-hub-distilled-news-entity.md` after Phase 2 is stable.

| Item | Purpose |
|------|---------|
| `news_events_store.py` + distillation worker | One row per event, timeline, consensus |
| `query_verified_news` union staging + distilled | Already partially local in `news_entity_worker.union_headlines_with_staging` |
| Cron `hub_news_distillation` | Drain staging queue every 2 min market hours + 18:35 IST |
| UI: expandable references + timeline | News Impact panel v2 |
| Backfill `records.parquet` → events | One-time script |

**Enable flag:** `HUB_NEWS_ENTITY_PIPELINE=1` (keep off in prod until backfill validated).

---

## Phase 4 — Prediction equation integration (defer until hub stable)

From `prediction-north-star` and master plan — **no coef edits without OOS gate (+3 pp)**.

| Item | Gate |
|------|------|
| Wire tagged hub factors into `factor_matrix` T0 columns | Walk-forward ablation |
| Fix T0 audit `headlines_t0_count` for miss analysis | Measurement only |
| Regime-aware news impact display | UI/trust — not Ridge input until ablation passes |
| Constituent sentiment from hub verified headlines | Phase 3 + ablation |

---

## Phase 5 — Operations & cleanup

- [ ] Delete stale local branches: `feat/create-agent-session-lifecycle`, `feat/nifty-index-research-pipeline`, `feat/unified-openalgo-data-channel`
- [ ] Remove dead paths: `tapetide.set_batch_research` (already removed locally), `news_collect` tiered fallback when hub empty on batch paths
- [ ] Scheduled job: nightly index `refresh_news_impact(refresh_ingest=True)` for NIFTY only — optional, separate from user Run
- [ ] Document env vars in `.env.example`: `INDEX_RESEARCH_MAX_WORKERS`, `HUB_NEWS_ENTITY_PIPELINE`, `INDEX_MONITOR_MACRO_DRIFT_PCT`

---

## Resolved decisions (Phase 2)

| Question | Decision |
|----------|----------|
| Where do headlines live? | Hub SSOT via `news_hub_bridge` |
| When to ingest per-symbol news? | **Only** `refresh_constituents=true` |
| Live poll news? | **Never** fetch; read cached index doc |
| Index News Impact refresh button? | Tiered APIs OK for **NIFTY** index ingest only |
| Constituent sentiment on normal Run? | Use cached company research; stale until refresh-all-50 |
| Distillation LLM on ingest? | **No** — Phase 3 async worker only |

---

## Self-review (spec coverage)

| Requirement | Task |
|-------------|------|
| Hub ingest on Refresh all 50 only | Task 1 |
| Normal run hub-read-only | Task 2 |
| Poll never batch research | Prerequisite 0 (Phase 1) |
| News Impact panel wired to hub | Tasks 2, 4 |
| No tiered APIs in Nifty batch | Prerequisite 0 + Task 1 |
| Distilled entity / LLM | Phase 3 (deferred) |
| Equation/news factor integration | Phase 4 (deferred) |

---

## Execution handoff

**Plan saved to `docs/superpowers/plans/2026-07-17-prediction-phase2-hub-news.md`.**

**Recommended order:**
1. **Land Prerequisite 0** (commit/push uncommitted Phase 1 work in 3 focused PRs)
2. **Phase 2 Tasks 1–5** via subagent-driven development
3. **Phase 3** distilled entity plan when headlines reliably populate hub

**Two execution options:**
1. **Subagent-Driven (recommended)** — one implementer per task, review after each
2. **Inline Execution** — same session, checkpoint after Task 2

Which approach do you want for Prerequisite 0 and Phase 2?
