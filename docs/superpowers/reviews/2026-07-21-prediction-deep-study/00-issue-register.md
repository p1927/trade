# Prediction Deep Study — Issue Register

**Study date:** 2026-07-21  
**Scope:** Phases 0–7, Prediction tab Run analysis pipeline

Severity: **critical** | **high** | **medium** | **low**

---

## Pre-identified checklist (C1–C10)

| ID | Status | Summary |
|----|--------|---------|
| C1 | **Confirmed** | Backtest walk-forward is macro-only; live headline adds bottom-up. `hybrid_eval_count` tracks real constituent archives. |
| C2 | **Confirmed** | `debate_numeric` / debate merge is live-only; not backtest-eligible. |
| C3 | **FIXED (gate)** | Ridge may train on panel `news_*` columns; live also adds calibrated event overlay — double-count risk. Overlay now requires `news_event_overlay=accepted` (not pending). |
| C4 | **FIXED (Tier 1+2)** | Group + covariance-aware SHAP when correlated; channel-first UI. |
| C5 | **FIXED** | `simulate.py` baseline now uses reconciled/debate headline when no overrides (`headline_return_pct`). |
| C6 | **Confirmed** | Cached constituents default; first run requires refresh or existing hub snapshot. |
| C7 | **FIXED** | UI `MACRO_MODEL_KEYS` (51) matches backend `MACRO_FACTOR_KEYS`; regression test added. |
| C8 | **FIXED (UI)** | Flow gate sets `macro_trust_multiplier=0.5`; PredictionSummary shows flow coverage banner and trust multiplier when gate fails. |
| C9 | **Confirmed** | `INDEX_PREDICTION_LAB_MODE=combine` can replace headline via combiner. |
| C10 | **Confirmed** | ~18 OOS eval rows; ±3pp promotion gates statistically noisy. |

---

## New findings (N-series)

| ID | File | Finding | Severity | Evidence | Fix hypothesis |
|----|------|---------|----------|----------|----------------|
| N-01 | `predictionVerification.ts` | Comment claimed parity; only 24/52 keys checked | medium | **FIXED** — `MACRO_CORE_KEYS` + `MACRO_EXTENDED_KEYS` = 52 | Sync with factor_matrix when keys change |
| N-02 | `predictor.py` L652–653 | Regime gate bypass when gated ≈ 0 | high | **FIXED** — always use `gated_raw`; test `test_predict_nifty_uses_gated_macro_when_gate_zeros_output` | — |
| N-03 | `predictor.py` L666–726 | Sign-conflict used pre-overlay macro | medium | **FIXED** — gate uses `macro_for_shrink` | — |
| N-04 | `aggregator.py` L515–559 | Debate merge after reconcile without re-anchor | high | **FIXED** — post-debate `reconcile_prediction_with_scenarios`; test `test_post_debate_reconcile_restores_sum_identity` | — |
| N-05 | `simulate.py` L175–176 | Simulate baseline ignored headline | high | **FIXED** — `headline_return_pct` when no overrides; test in `test_prediction_review_fixes.py` | — |
| N-06 | `prediction_ledger.py` L76–83 | Scenario metadata schema mismatch | medium | **FIXED** — `_scenario_ledger_row` maps event/midpoint | — |
| N-07 | `scenarios.py` L33, L146–147 | Null dates inflated earnings/RBI | medium | **FIXED** — skip null dates; tests added | — |
| N-08 | `constituent_momentum.py` L158–160 | Dead unreachable branch | low | **FIXED** — removed dead branch | — |
| N-09 | `attribution.py` L82–83 | Earnings bump used wall-clock today; null event dates inflated bump | medium | **FIXED** — `as_of_day` from `predict_nifty` + aggregator; null dates skipped (parity with N-07) | — |
| N-10 | `history_panel.py` | Re-enrichment on load | medium | **FIXED** — skip `enrich_prediction_panel` when loading materialized panel | — |
| N-11 | `macro_global.py` | Live vs panel derivation parity | medium | **FIXED** — `panel_live_parity.py` overlays panel-derived keys on live snapshot | — |
| N-12 | `PredictionSummary.tsx` | Accuracy label ambiguity | medium | **FIXED** — separate walk-forward vs ledger labels | — |
| N-13 | Hub artifact | Debate breaks sum identity | low | **BY DESIGN** — **FIXED UI** — debate badge in PredictionSummary | — |

---

## Phase completion log

| Phase | Doc | Files reviewed | New issues |
|-------|-----|----------------|------------|
| 0 | `phase-0-e2e.md` | 6 | N-13 |
| 1 | `phase-1-data.md` | 6 | N-10, N-11 |
| 2 | `phase-2-bottom-up.md` | 4 | N-08, N-09 |
| 3 | `phase-3-macro.md` | 5 | N-02, N-03 |
| 4 | `phase-4-scenarios.md` | 4 | N-04, N-06, N-07 |
| 5 | `phase-5-explain-sim.md` | 5 | N-05 (+ C4, C5) |
| 6 | `phase-6-backtest.md` | 6 | C1, C2, C10 |
| 7 | `phase-7-ui.md` | 5 | N-01, N-12 |

---

## Verification (post-fix, 2026-07-21, review pass 2)

| Command | Exit | Result |
|---------|------|--------|
| `python scripts/audit_prediction_data.py --days 500` | 0 | Panel audit pass |
| `python scripts/verify_prediction_pipeline.py` | 0 | Full pipeline verify |
| `pytest tests/test_prediction_review_fixes.py tests/test_index_predictor.py …` | 0 | 30 passed |
| `POST /trade/index-prediction/simulate` (no overrides) | 200 | `simulation.baseline_return_pct=-1.375` matches hub `expected_return_pct` |
| Hub sum identity | — | `bottom_up + macro_delta = expected` (diff 0.0) on `latest.json` as_of 2026-07-21T19:05:21Z |
| `is_news_overlay_enabled()` after C3 gate | — | `False` while overlay status pending |

---

## C4 — Group attribution under correlation (2026-07-21)

**Status:** **FIXED (Tier 1+2)** — covariance-aware SHAP when panel+shap available; grouped marginal fallback; channel-first UI.

### Implementation (Tier 1)

| Change | Location |
|--------|----------|
| Union-find perturbation groups from `_REDUNDANCY_GROUPS` + `correlated_pairs` | `explain.py` `_build_perturbation_groups` |
| Route to grouped marginal when warning and Tier 2 unavailable | `explain.py` `explain_macro_factors` |
| `correlation_caveat` on contributors in correlated clusters | `explain.py` |
| Channel bars default; multicollinearity banner; correlated pairs | `FactorImpactWorkbench.tsx` |
| Types for new payload fields | `api.ts` |

### Implementation (Tier 2)

| Change | Location |
|--------|----------|
| Panel background matrix (365d, min 30 rows) | `explain.py` `_load_panel_background_matrix` |
| Shared Ridge/poly context for SHAP | `explain.py` `_prepare_linear_shap_context` |
| `correlation_dependent` LinearExplainer when warning + panel + shap | `explain.py` `_try_correlation_dependent_shap_contributions` |
| UI banner for covariance-aware method | `FactorImpactWorkbench.tsx` |

### Verification (2026-07-21, Tier 1+2)

| Check | Result |
|-------|--------|
| `pytest tests/test_index_explain.py` | 12 passed (with shap installed) |
| `test_explain_uses_correlation_dependent_shap_with_panel` | method=`correlation_dependent_shap`, sum identity |
| `test_explain_falls_back_to_grouped_marginal_when_tier2_unavailable` | deterministic fallback |
| `test_load_panel_background_matrix_requires_min_rows` | min 30 rows gate |
| Live (when panel materialized) | hub `factor_explanation.method` ∈ {`correlation_dependent_shap`, `grouped_marginal`} |

### Tier 3 (defer)

- Owen values / full causal DAG — out of scope for Prediction tab.
- Optional: compare grouped marginal vs correlation_dependent credit split on synthetic correlated pairs in regression tests.

---

## Recommended fix priority (remaining)

1. C1/C2/C9/C10 — documented limitations (backtest≠live, debate not backtest-eligible, combiner headline, small n OOS)
