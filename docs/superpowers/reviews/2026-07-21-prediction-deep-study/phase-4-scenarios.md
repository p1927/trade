# Phase 4 — Scenarios, Reconcile, Debate

**Goal:** How event scenarios and agent debate modify the headline without silent drift.

---

## Orchestrator order (aggregator)

```423:559:integrations/trade_integrations/dataflows/index_research/aggregator.py
scenarios = build_index_scenarios(...)           # BEFORE predict
scenario_anchor = scenario_weighted_return_pct(...)
prediction = predict_nifty(..., scenario_anchor_return_pct=scenario_anchor)
prediction = reconcile_prediction_with_scenarios(...)  # AFTER predict
prediction = finalize_index_prediction(...)
...
prediction = merge_index_prediction(debate_struct, prediction)  # AFTER reconcile
prediction = finalize_index_prediction(...)  # again
```

**Correctness:** Scenarios feed anchor into `predict_nifty` shrink/sign-conflict. Reconcile pulls headline when |model−anchor| > 1.5% (75% anchor weight).

**N-04 (high):** Debate merge runs **after** reconcile with no second reconcile — debate can move headline away from scenario anchor (live artifact: `debate_merged=True`, expected=8.45 vs bottom_up+macro=5.11).

---

## File 1: `scenarios.py`

**`build_index_scenarios`:** Earnings cluster, RBI, budget, monthly expiry, macro stress templates → 3–6 scenarios.

**`_finalize_scenarios`:** Cap 6, normalize probabilities, sort by probability descending.

**`scenario_weighted_return_pct`:** Probability-weighted midpoint returns.

**`reconcile_prediction_with_scenarios`:**

```286:327:integrations/trade_integrations/dataflows/index_research/scenarios.py
# If abs(expected - anchor) > 1.5%:
#   expected = 0.25 * model + 0.75 * anchor
#   cap_macro_delta; update range
```

**Issues (N-07):**
- Null earnings dates counted in-horizon (L33)
- Null RBI event date → `_has_upcoming_rbi` returns True (L146–147)
- Monthly expiry scenarios always injected in builder; `upcoming_events` uses DTE filter — inconsistent
- `_today()` not India trading calendar

---

## File 2: `debate_synthesis.py`

**`merge_index_prediction`:** 60% debate / 40% quant blend on `expected_return_pct`; recomputes `macro_delta_pct` as residual; sets `debate_merged=True`, `provenance`.

**Issues:**
- Debate textual `view` not used for index (view from blended return only)
- Price regex in debate parse can false-match
- No OOS gate on debate quality (C2)

---

## File 3: `upcoming_events.py`

**`build_upcoming_events`:** Constituent events, expiry, budget week, results season, RBI — sorted by `(days_from_now, -weight)`.

**Issues:** Budget/results use `days_from_now: 0` with today as date — placeholder not true calendar date.

---

## Live artifact evidence

```
expected_return_pct: 8.4463
bottom_up: 0.11  macro_delta: 5.0
debate_merged: True
provenance.direction: debate
scenario_anchor_return_pct: 0.1154
```

Debate dominated final headline; simple hybrid sum invalid for verification.

---

## Phase 4 verdict

Scenario reconcile is **well-placed** before debate. **Debate overwrite without re-anchor** is the highest coherence gap (N-04). Scenario null-date bugs inflate event probability (N-07).

---

## Issues logged

| ID | Severity |
|----|----------|
| N-04 | high |
| N-06 | medium (ledger scenario keys) |
| N-07 | medium |
| C2 | confirmed |
| N-13 | low (debate breaks sum check) |
