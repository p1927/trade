# Prediction Review Phase 1 — Pipeline Integrity

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or executing-plans.

**Goal:** Wire sign-conflict gate and scenario anchor on the live index pipeline; keep `view` / `direction_view` consistent after reconciliation.

**Architecture:** Build scenarios before `predict_nifty`, pass `scenario_anchor_return_pct`, add `finalize_index_prediction()` after reconcile, always neutralize direction on macro vs anchor sign conflict.

**Tech Stack:** Python 3, pytest, `index_research/` aggregator + light_refresh + predictor + scenarios.

## Global Constraints

- Do not change `cap_macro_delta(±5%)` or reconciliation threshold (1.5%).
- Ledger metadata keys additive only.

---

## Task 1 — `finalize_index_prediction` helper

**File:** `integrations/trade_integrations/dataflows/index_research/predictor.py`

- [ ] Add `finalize_index_prediction(prediction, *, raw_macro, scenario_anchor, regime_label, wf_metrics, spot, mae_pct)`:
  - Recompute `view` via `_classify_view(expected_return_pct)`
  - Re-apply `apply_sign_conflict_gate` on existing direction fields
  - Recompute range low/high from expected return + mae_pct
  - Set `scenario_anchor_return_pct` on dict if anchor provided and missing

## Task 2 — Sign-conflict gate always neutral

**File:** `predictor.py` — `apply_sign_conflict_gate`

- [ ] Remove `high_conf` branch that preserves `direction_view`
- [ ] On conflict: return `"neutral"`, halved confidence, `sign_conflict=True`
- [ ] Update docstring

## Task 3 — Reconcile syncs view

**File:** `scenarios.py` — `reconcile_prediction_with_scenarios`

- [ ] After updating `expected_return_pct`, set `view` via `_classify_view` (import from predictor)

## Task 4 — Reorder orchestrators

**Files:** `aggregator.py`, `light_refresh.py`

- [ ] Move `build_index_scenarios` before `predict_nifty`
- [ ] Compute anchor via `scenario_weighted_return_pct`
- [ ] Pass `scenario_anchor_return_pct=anchor` to `predict_nifty`
- [ ] Call `finalize_index_prediction` after reconcile (before debate merge)

## Task 5 — Tests

- [ ] Update `tests/test_index_predictor.py` sign-conflict test
- [ ] Add `test_finalize_index_prediction_syncs_view_after_reconcile` in `tests/test_index_scenarios.py`
- [ ] Add aggregator integration test for sign_conflict on disagreeing anchor/macro

**Verify:**

```bash
python -m pytest tests/test_index_predictor.py tests/test_index_scenarios.py tests/test_index_aggregator.py -q
```
