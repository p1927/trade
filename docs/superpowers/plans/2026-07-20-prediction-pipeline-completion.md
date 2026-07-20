# Prediction Pipeline Completion — Multi-Phase Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for phases 1–8. Verify each phase before starting the next.

**Goal:** Close every **code and wiring** gap so the full prediction pipeline runs end-to-end (ingest → panel → walk-forward → scoreboard → live lab → optional combine promotion). Missing vendor data uses proxies/stubs with clear audit flags — fill real series later without rewiring.

**Why items were deferred (original rationale → what we do now)**

| Deferred item | Why it waited | Completion approach |
|---------------|---------------|---------------------|
| **Phase G** overlay / debate / LightGBM | Avoid double-count before split tracks existed; debate needs dated archive; ML gated on Ridge OOS | **Shipped** in `5b37ed9` — operational: accumulate debate history |
| **Phase F** auto-promotion | Needs scoreboard history + bootstrap CI + stable weights | Wire ops (re-run policy, light refresh lab); gates stay strict |
| **Phase I** real P/B, CRISIL credit | No free CRISIL API; NSE factsheet scrape incomplete | Keep proxy + cold-tier hooks; audit marks `data_quality: proxy` |
| **News → Ridge history** | Hub verified news is live-first; panel had calendar news | **Panel-first merge** in `enrich_macro_with_news_features` |
| **Shock calibration** | Needs reconciled stories volume | Chain into `trade data ingest --news` (optional flag) |
| **Quality 5–7** | Lower priority vs pipeline integrity | Wire calibrate_bottom_up, hybrid backtest flag, direction score split |
| **H1 M5 invalidation UX** | Backend shipped first | Banner + cause/attribution panels in Prediction UI |
| **H2/H3 SVAR/DoWhy** | Research scope, not blocking headline | Stub modules + scoreboard note only |
| **Data router** | Parallel to ingest; not blocking Ridge | Flag `DATA_ROUTER_ENABLED=1`; commit WIP when tests pass |
| **Autonomous agents** | Separate north-star track | Out of scope for this plan — see `2026-07-16-autonomous-remaining-phases.md` |

---

## Global constraints

- No paid data vendors; proxies documented in audit + factor catalog.
- Headline stays `quant_ridge` until combiner passes all promotion gates.
- Every phase: run listed pytest + script smoke before marking done.

---

## Phase 1 — Walk-forward news + ingest chain (P0)

**Problem:** Walk-forward zeroed panel `news_*` columns because hub-only enrich overwrote them.

**Files:**
- `event_overlay.py` — panel-first news merge
- `scripts/ingest_historical_data.py` — `--news`, `--shock-calibration`
- `scripts/audit_prediction_data.py` — news/shock/debate archive sections

**Verify:**
```bash
python -m pytest tests/test_news_event_features.py tests/test_track_walk_forward.py -q
python scripts/audit_prediction_data.py --write
```

- [ ] Panel keys preserved in walk-forward context
- [ ] Audit reports shock topic count + debate archive depth

---

## Phase 2 — Live lab parity (P0)

**Problem:** Light refresh skipped `attach_forecast_lab`; promotion history rarely accumulates.

**Files:**
- `light_refresh.py` — mirror aggregator lab attach
- `promotion.py` — bootstrap when daily rows sparse
- `vibetrading/agent/src/api/trade_routes.py` — scoreboard refresh policy

**Verify:**
```bash
python -m pytest tests/test_prediction_pipeline_lab.py -q
python scripts/run_track_backtest.py --ticker NIFTY --days 60 --eval-step 5
python scripts/run_track_backtest.py --ticker NIFTY --days 60 --eval-step 5  # 2nd run
```

- [ ] Light refresh writes `forecast_tracks` when lab enabled
- [ ] Second backtest appends `promotion_run_history`

---

## Phase 3 — Quality phases 5–7 (P1)

**Files:**
- `attribution.py` + `calibrate_bottom_up.py` — rolling coeffs in live path
- `backtest_runner.py` + API — `include_bottom_up` param
- `predictor.py` + `PredictionSummary.tsx` — `direction_model_score` vs confidence

**Verify:**
```bash
python -m pytest tests/test_calibrate_bottom_up.py tests/test_index_backtest.py -q
```

---

## Phase 4 — H1 invalidation UX (P1)

**Files:**
- `cause_stress_index.py` — `unmodeled_event_suspected`
- `PredictionSummary.tsx` — stale artifact + high stress banner
- New `CauseAttributionPanel.tsx` (minimal)

**Verify:** `cd vibetrading/frontend && npm run build`

---

## Phase 5 — Phase I data stubs + audit (P2)

**Files:**
- `panel_enrichment.py`, `india_rates.py` — consistent cold-tier reads
- `phase_i_coverage.py` — automated ablation hook in audit
- Factor catalog proxy labels

**Verify:** `python scripts/audit_prediction_data.py --write`

---

## Phase 6 — Debate archive ops (P2)

**Files:**
- `scripts/archive_agent_debate_date.py` (exists)
- `scripts/seed_debate_archive_from_latest.py` — copy latest to N synthetic dates for WF dev
- Audit: `debate_archive_eligible`

**Verify:** `python -m pytest tests/test_prediction_algorithms_tracks.py -k debate -q`

---

## Phase 7 — H2/H3 research stubs (P3)

**Files:**
- `prediction_algorithms/causes/svar_stub.py` — LP placeholder returning empty IRFs
- `prediction_algorithms/causes/dowhy_stub.py` — documented not-run
- Scoreboard `research_notes` field

**Verify:** import smoke only

---

## Phase 8 — Data router integration (P3)

**Files:**
- Commit `integrations/trade_integrations/data_router/`
- `history_ingest.py` — optional router path when `DATA_ROUTER_ENABLED=1`

**Verify:**
```bash
python -m pytest tests/test_data_router_ohlcv.py tests/test_data_router_worker.py -q
```

---

## Phase 9 — E2E verification script

**File:** `scripts/verify_prediction_pipeline.py`

Runs: ingest audit → track backtest ×2 → pytest subset → prints promotion status.

**Verify:** `python scripts/verify_prediction_pipeline.py`

---

## Master plan doc sync

After Phase 1–3: update status table in `2026-07-17-prediction-algorithms-master-plan.md` (G shipped, F partial→shipped wiring, I partial unchanged on data).
