# Prediction Quality — Phased Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or executing-plans for remaining phases.

**Goal:** Fix attribution, momentum, scenario, and transparency bugs without regressing the live Nifty forecast pipeline; improve prediction quality and user trust.

**Architecture:** Backend fixes in `index_research/` (explain, scenarios, light_refresh, constituent_momentum); frontend transparency in Prediction summary, factor table, backtest panel; regression tests per phase.

**Tech Stack:** Python 3, pytest, React/TS, existing Ridge + hub artifact pipeline.

## Global Constraints

- No paid data vendors; yfinance/nselib/OpenAlgo only.
- Preserve headline formula: bottom-up + macro, scenario reconciliation when divergent.
- Every phase ships with pytest coverage before UI changes where applicable.

---

## Factor research summary (literature vs model)

| Factor | Expected link to Nifty | Research consensus | Our usage |
|--------|------------------------|-------------------|-----------|
| oil_brent / oil_wti | Mixed | Importer theory negative; empirically regime-dependent ([BRIC study](https://ideas.repec.org/a/ijb/journl/v19y2020i1p91-108.html)) | Ridge + sensitivity ✓ |
| usd_inr | Mixed short / weak long | FII channel; FX vol hurts short-run ([IMFI NARDL 2022–24](https://doi.org/10.21511/imfi.22(3).2025.28)) | Ridge ✓ |
| gold | Mixed | Safe haven vs yields | Ridge ✓ |
| sp500 | Positive | Global risk appetite | Ridge ✓ |
| us_10y | Mixed | Financial conditions / FII rotation | Ridge ✓; attribution sensitive to absolute shocks |
| india_vix | Negative | Fear gauge; weak alone as return predictor | Ridge + regime ✓ |
| fii_net_5d | Positive | Granger-causes Nifty (~1m lag in monthly studies) | 5d sum for 14d horizon ✓ |
| dii_net_5d | Context | Often insignificant vs FII in regression | Included; low weight expected |
| fii_fut_long_short_ratio | Positive | Positioning | Derivatives context ✓ |
| nifty_pe | Mixed | Valuation; slow mean reversion | Ridge ✓ |
| cpi_yoy_proxy / repo_rate | Negative | Policy/inflation channel | Ridge + RBI scenarios ✓ |
| index_sentiment | Weak positive | FinBERT short-horizon predictability unstable ([SSRN 5086825](https://ssrn.com/abstract=5086825)) | Bottom-up + Ridge; needs calibration (Phase 5) |
| nifty_pcr | Mixed | Contrarian at extremes | OpenAlgo + Ridge ✓ |
| nifty_return_7d/14d, rsi, vol, ma20 | Mixed | Momentum/mean-reversion features | Technical Ridge inputs ✓ |
| constituent_momentum_7d | Positive | Constituent-driven index modeling ([MDPI 2025](https://www.mdpi.com/2227-7390/13/17/2762)) | Bottom-up + Ridge; **was broken on light refresh** |
| days_to_monthly_expiry, budget, results | Context | Event dummies | Scenarios + calendar ✓ |

---

## Phase 1 — Attribution integrity (P0) ✅ SHIPPED

**Problem:** Contributors summed to ~5% while reconciled macro was ~0.88%.

**Fix:** `_rescale_explanation_to_headline()` in `explain.py` scales contributor rows when `headline_return_pct` differs from Ridge macro.

**Tests:** `test_explanation_bundle_rescales_after_reconciled_headline`

---

## Phase 2 — Momentum pipeline (P0) ✅ SHIPPED

**Problem:** Light refresh omitted `attach_constituent_momentum` and dropped `momentum_7d_pct` from hub JSON.

**Fix:**
- `light_refresh.py`: attach momentum, rollup to macro, persist momentum on signals
- `momentum_coverage_stats()` + `prediction.momentum_coverage`
- UI warning when coverage < 50%

**Tests:** `test_momentum_coverage_stats`

---

## Phase 3 — Scenario & reconciliation metadata (P1) ✅ SHIPPED

**Fix:**
- `_finalize_scenarios`: normalize probabilities to sum 1.0
- `reconcile_prediction_with_scenarios`: store `raw_expected_return_pct`, `raw_macro_delta_pct`, blend weights
- UI amber banner on reconciled headline

**Tests:** scenario prob sum, reconcile raw metadata

---

## Phase 4 — Transparency UI (P1) ✅ SHIPPED

- `PredictionSummary`: reconciliation + momentum banners
- `BacktestEvaluationPanel`: macro-only scope disclaimer
- `FactorCompositionTable`: research notes from `factorResearchNotes.ts`
- `api.ts`: new prediction / factor_explanation fields

---

## Phase 5 — Bottom-up calibration (P2) — TODO

**Files:** `attribution.py`, new `calibrate_bottom_up.py`

- Fit sentiment/momentum coefficients on historical constituent returns (rolling 60d)
- Fall back to current heuristics when insufficient data
- Test: synthetic signals → expected return within cap

---

## Phase 6 — Hybrid backtest (P2) — TODO

**Files:** `backtest_runner.py`

- Optional `include_bottom_up=True` when constituent snapshots exist
- Report `scope: hybrid | macro_only` explicitly in JSON
- UI toggle to compare macro-only vs hybrid MAE

---

## Phase 7 — Direction score calibration (P3) — TODO

- Rename API field `direction_confidence` → expose `direction_model_score` + `direction_hit_rate_oos`
- Platt scaling or isotonic on walk-forward logits
- Cap displayed probability at OOS hit rate prior

---

## Verification checklist (each release)

```bash
python -m pytest tests/test_index_explain.py tests/test_index_scenarios.py tests/test_index_constituent_momentum.py -q
cd vibetrading/frontend && npm run build
curl -s "http://127.0.0.1:8899/trade/index-prediction?ticker=NIFTY&horizon_days=14" | python3 -c "
import json,sys; a=json.load(sys.stdin)['artifact']; fe=a['factor_explanation']; print('contrib sum', sum(c['contribution_pct'] for c in fe['contributors']), 'macro', fe['macro_delta_pct'])
"
# contrib sum must ≈ macro_delta_pct
```

---

## Regression guardrails

- Never change `cap_macro_delta(±5%)` without backtest re-run
- Scenario reconciliation threshold (1.5%) unchanged in Phase 1–4
- Ledger append shape unchanged (metadata keys additive only)
