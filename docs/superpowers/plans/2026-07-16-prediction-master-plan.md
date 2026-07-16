# Prediction Equation Master Plan

**Supersedes / consolidates:**
- [`2026-07-16-prediction-equation-investigation.md`](2026-07-16-prediction-equation-investigation.md) — Phases 0–6 (RCA + counterfactual)
- [`2026-07-16-prediction-factor-rationality-plan.md`](2026-07-16-prediction-factor-rationality-plan.md) — re-verification + factor table
- [`2026-07-16-prediction-completeness.md`](2026-07-16-prediction-completeness.md) — data gaps

**North star:** User browses Nifty → gets a researched 14d forecast → sees P&L/charges → can trust *why* the equation said what it said. Accuracy improves only via **data completeness + economically justified structure**, not coef edits.

---

## Current state (after investigation + partial rationality work)

| Metric | Baseline | Delta regression | After revert + gates |
|--------|----------|----------------|----------------------|
| Direction OOS (365d, 18 eval rows) | 44.4% | 35.3% (−9.1 pp) | **44.4%** restored |
| MAE OOS | 4.50% | 4.37% | **4.07%** |
| FII/DII coverage | 49.6% | 49.6% | **49.6%** (still blocking) |
| Hybrid eval rows | 0 | 0 | 0 |

**Key lesson:** Delta features (`fii_net_5d_change_5d`, etc.) hurt OOS despite plausible economics — **reverted**. Joint flow features (`institutional_net_5d`, `dii_absorption_ratio`) added but **not yet ablation-validated**.

**Counterfactual on misses (11):** 3 mapping_error_T0, 4 drift_dominant, 4 cap_artifact. Top drift: `sp500`, `fii_fut_long_short_ratio`, `dii_net_5d`, `fii_net_5d`.

---

## Non-negotiable rules

- Walk-forward OOS on held-out eval rows only; never optimize in-sample R² (currently +0.13 in-sample vs 44% OOS — overfit warning).
- **OOS gate for any new feature block:** direction hit rate ≥ baseline + 3 pp on 365d / `eval_step=5`, else **reject**.
- No manual coef edits in [`reports/hub/_data/index_factors/model/latest.json`](../../reports/hub/_data/index_factors/model/latest.json).
- No zero-imputation for missing FII/DII — backfill real rows only (`_source=fetch-pipeline`).
- Block ablation must return numeric hit rates (fixed: drop columns, not NaN) — [`equation_diagnostics.py`](../../integrations/trade_integrations/dataflows/index_research/equation_diagnostics.py).

---

## Phased work plan

### Phase 0 — Measurement & RCA infrastructure (DONE)

Shipped: horizon_dates, prediction_counterfactual, equation_diagnostics, t0_information_audit, regime_gates, API + UI counterfactual bars.

### Phase 1 — Data completeness (PRIORITY)

Extend FII/DII via Mr. Chartist + NSE FAO archives + flow cache; enrich factor store; wire light_refresh upsert.

**Gate:** `data_audit_latest.json` shows `fii_net_5d` and `dii_net_5d` coverage **>90%**.

### Phase 2 — Factor rationality & OOS gates

Ablation joint flows (+3pp), regime buckets, redundancy cleanup, cap_artifact remeasure.

### Phase 3 — Horizon dynamics & hybrid parity

T0 audit tags, constituent archive backfill, hybrid backtest `--include-bottom-up`.

### Phase 4 — Live loop & reporting honesty

Ledger counterfactual, walk-forward direction in UI, scheduled post-close enrich.

### Phase 5 — Decision record

Regenerate `equation_improvement_decisions.md` with full accept/reject evidence.

---

## Validation protocol

```bash
python -m pytest tests/test_horizon_dates.py tests/test_prediction_miss_analysis.py \
  tests/test_index_backtest.py tests/test_prediction_counterfactual.py \
  tests/test_equation_diagnostics.py tests/test_institutional_joint_features.py -q
```

## What we will NOT do

- Tune Ridge coefficients or lower α to fit historical misses
- Re-introduce delta features without coverage >90% and +3 pp ablation win
- Report in-sample 86.7% direction as model accuracy
- Skip FII/DII/DERIV data because coverage is hard
- Add "war/oil" dummies fit on 10 miss dates
