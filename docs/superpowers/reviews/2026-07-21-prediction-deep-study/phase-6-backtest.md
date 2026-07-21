# Phase 6 — Backtest, Ledger, Forecast Lab

**Goal:** How we measure ~50% direction OOS and whether UI matches methodology.

**Baseline metrics (master plan July 2026):** direction OOS **50.0%**, MAE **3.49%**, **18** eval rows, `hybrid_eval_count=0`.

---

## File 1: `backtest_runner.py`

**`run_walk_forward_backtest`:**
- Expanding window over `load_aligned_factor_history`
- **`compute_macro_only_return`** for primary eval — **not full hybrid** (C1)
- Scenarios built with **empty signals** `build_index_scenarios([], factors_today, ...)` — no earnings-cluster scenarios vs live

**Scope label:** UI `BacktestEvaluationPanel` correctly notes macro-only vs hybrid when `backtest.scope` set.

---

## File 2: `prediction_ledger.py`

**`append_prediction`:** Every live run appends forecast row to `ledger.parquet`.

**`reconcile_predictions`:** Fill actual return at horizon maturity.

**`compute_accuracy_metrics`:** Prefers walk-forward backtest for `direction_hit_rate` when available — **not ledger-only** (N-12).

**N-06:** `build_prediction_metadata` scenarios expect `name`, `expected_return_pct`; live scenarios use `event`, `outcome`, `index_range` — ledger RCA scenarios often empty.

---

## File 3: `prediction_miss_analysis.py`

RCA on backtest eval rows — which factors drove misses. Feeds `PredictionMissAnalysisPanel`.

---

## File 4: `walk_forward.py`

**`hybrid_eval_count`:**

```python
if signals and signals[0].symbol != "_INDEX_SENTIMENT":
    hybrid_eval_dates += 1
```

Counts days with real constituent archives vs sentiment proxy. **C1:** When 0, backtest cannot validate bottom-up leg.

**Track metadata:** `backtest_eligible` per track; `debate_numeric` typically false (C2).

**C10:** `eval_count` often <60 — promotion gates noisy; UI "insufficient evidence" on scoreboard.

---

## File 5: `pipeline_lab.py`

**`attach_forecast_lab`:** Runs parallel tracks (quant_ridge, bottom_up, scenario_anchor, macro_only, naive baselines, experimental ML).

**`apply_forecast_lab_result_to_prediction`:** When `lab_mode() == "combine"`, combiner can **replace headline** (C9).

**Snapshots:**
- `snapshot_pre_reconcile_prediction` — quant before scenario pull
- `snapshot_legacy_prediction` — after reconcile/debate

---

## File 6: UI panels

**`BacktestEvaluationPanel.tsx`:** Walk-forward metrics, factor audit, drawdowns, daily eval table. Does not show `hybrid_eval_count` (that's on Track Scoreboard).

**`TrackScoreboardPanel.tsx`:** Per-track replay, `hybrid_eval_count` display, `backtest_eligible` badges.

---

## Phase 6 verdict

**Walk-forward methodology is correct** for time series. **Live headline skill ≠ backtest headline skill** because hybrid bottom-up and debate are excluded or live-only. Documented in premortem; UI partially surfaces via scope banners and scoreboard.

---

## Issues logged

| ID | Severity |
|----|----------|
| C1 | confirmed |
| C2 | confirmed |
| C9 | confirmed |
| C10 | confirmed |
| N-06 | medium |
| N-12 | medium |
