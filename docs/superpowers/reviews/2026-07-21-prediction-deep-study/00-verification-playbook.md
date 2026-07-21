# Prediction Analysis — Verification Playbook

Run after every **Run analysis** or when investigating forecast quality.

## Layer 1 — Automated (terminal)

```bash
# Data panel integrity (pinned factors ≥45%, invariants)
python scripts/audit_prediction_data.py --days 500

# Full wiring: audit + backtest stability + pytest subset
python scripts/verify_prediction_pipeline.py

# Walk-forward OOS metrics only
python scripts/run_track_backtest.py --ticker NIFTY --days 60 --eval-step 5

# Panel unit tests
python -m pytest tests/test_prediction_data_consistency.py tests/test_history_panel.py -q --timeout=60
```

**Verified 2026-07-21:** audit exit 0; panel pytest 7 passed.

## Layer 2 — Hub artifact cross-checks

Paths (default hub root `reports/hub/`):

| File | Purpose |
|------|---------|
| `NIFTY/index_research/latest.json` | Full dossier |
| `_data/index_factors/model/latest.json` | Ridge coefficients, walk-forward MAE/R² |
| `_data/index_predictions/ledger.parquet` | Forecast vs actual history |
| `log/index_prediction_jobs/{job_id}/job.json` | Async job + panel artifact |

### Manual arithmetic checks

1. **Hybrid equation (pre-debate, pre-combiner):**
   - `expected_return_pct ≈ bottom_up_return_pct + macro_delta_pct` (after shrink/cap/reconcile)
   - If `debate_merged: true` or forecast lab `combine` mode, headline **will not** match this sum — check `provenance`, `debate_merged`, `forecast_tracks`.

2. **Factor attribution:**
   - `factor_explanation.contributors[*].contribution_pct` should sum ≈ `macro_delta_pct` (rescaled), not full expected return.

3. **Flow gate:**
   - `data_completeness.passes_gate == false` → expect `macro_trust_multiplier == 0.5` in pipeline (aggregator).

4. **Pipeline order:**
   - Compare `pipeline_log[].stage` sequence to `aggregator.run_index_research` stages.

### Live artifact example (2026-07-21)

```
bottom_up=0.11  macro_delta=5.0  expected=8.45  (debate_merged=True)
```

Sum `bottom_up + macro_delta = 5.11` ≠ `expected` because debate merge overwrote quant headline. **Not a bug** — document provenance when verifying.

## Layer 3 — UI verification

Open Prediction tab → **Verification** section (`PredictionVerificationPanel`).

- Macro coverage vs `MACRO_MODEL_KEYS` (24-key subset — see Phase 7 / issue N-01)
- Equation R² / MAE vs backtest report
- Each row: `feedsForecast` true/false

## API smoke (stack up)

```bash
trade status
curl -s "http://127.0.0.1:8899/trade/index-prediction?ticker=NIFTY" | jq '.prediction | {view, expected_return_pct, bottom_up_return_pct, macro_delta_pct, debate_merged}'
```

## Live run record (2026-07-21 fresh analysis)

**Trigger:** `POST /trade/index-prediction/run/start` (cached constituents, forecast lab on)  
**Job:** `1426277806c44e8aacbae64bbbf7c3e3` — worker completed; hub `as_of=2026-07-21T18:51:45Z`  
**Note:** API briefly unavailable during poll (reload); artifact persisted to hub.

| Check | Result | Status |
|-------|--------|--------|
| Post-debate reconcile in `pipeline_log` | `+8.57% → +2.14% toward scenarios` | **CONFIRMED** (N-04) |
| `expected ≈ bottom_up + macro_delta` | 2.1415 = 0.11 + 2.0315 | **CONFIRMED** (after reconcile residual) |
| `debate_merged` + `quant.expected` preserved | quant −1.085% vs headline +2.14% | **CONFIRMED** (N-13 UI) |
| Simulate baseline = headline | 2.1415 = 2.1415 | **CONFIRMED** (N-05) |
| Ledger scenario metadata | names like `Macro drift · Neutral drift` | **CONFIRMED** (N-06) |

**Interpretation:** Debate initially pushed headline to ~+8.57%; post-debate reconcile pulled toward scenario anchor (~0%), final +2.14%. This is expected pipeline behavior — not a math bug.

---

## When results look wrong

| Symptom | Check first |
|---------|-------------|
| No artifact / incomplete | First run needs **Refresh all 50 constituents** |
| Macro delta = 0 | Flow gate fail, empty model artifact, or regime gates zeroed flows |
| Direction neutral despite bullish return | `sign_conflict: true` on prediction |
| Workbench shock ≠ headline | Simulate uses raw Ridge baseline, not reconciled/debate headline (N-05) |
| Backtest ≠ live skill | `hybrid_eval_count` on scoreboard; backtest is macro-only (C1) |

## Research references

- Walk-forward validation: [DCF Modeling — regression validation](https://www.dcfmodeling.com/blogs/blog/leveraging-regression-financial-model)
- Ridge + scaling: [IBKR Quant — advanced linear regression](https://www.interactivebrokers.com/campus/ibkr-quant-news/beyond-the-straight-line-advanced-linear-regression-models-for-financial-data/)
- Hybrid forecast reconciliation: [Hyndman — hierarchical forecasts](https://robjhyndman.com/papers/Hierarchical6.pdf)
