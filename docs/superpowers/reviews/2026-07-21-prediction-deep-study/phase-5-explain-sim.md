# Phase 5 — Explanation, Simulation, Playground

**Goal:** What Factor Impact Workbench computes vs pipeline forecast.

**Research:** SHAP under correlated features is misleading unless conditional sampling or grouping used ([Aas et al. 2021](https://martinjullum.com/publication/aas-2021-explaining/aas-2021-explaining.pdf), [StockAlpha](https://stockalpha.ai/alpha-learning/feature-attribution-under-correlation-shapley-values-for-alpha-signals)).

---

## File 1: `explain.py`

**`explain_macro_factors`:**
- Primary: SHAP `Explainer` on Ridge predict function
- Fallback: marginal one-at-a-time perturbation
- Output: `contributors[]` with `contribution_pct`, `share_of_macro`, `index_points`

**`build_factor_sensitivity`:** ±10% shock sweep per factor → curves for workbench chart.

**`_rescale_explanation_to_headline`:** Scales contributors to **reconciled macro delta** (not full expected return if debate moved headline).

**C4 confirmed:** Standard SHAP assumes feature independence; macro panel is highly correlated (oil, FX, SPX, VIX co-move).

**Issue:** `_FACTOR_LABELS` incomplete vs 52 macro keys — Phase I / TA factors show raw names in UI.

---

## File 2: `simulate.py`

**`simulate_index_prediction`:** Applies factor overrides + optional cascade → re-runs Ridge macro path (not full aggregator).

**N-05 (high):**

```175:176:integrations/trade_integrations/dataflows/index_research/simulate.py
baseline_return = bottom_up + baseline_macro_delta  # raw Ridge macro
# Does NOT use reconciled or debate-adjusted headline_return_pct
```

Workbench UI baseline uses `artifact.prediction.expected_return_pct` (reconciled/debate) — **server simulate baseline can disagree with chart anchor**.

**C5 confirmed:** Cascade from `cascade/heuristic_rules.py` — not calibrated SVAR/DoWhy (deferred Phase H2/H3).

View thresholds in simulate (±0.3%) hardcoded — not shared with `classify_index_view`.

---

## File 3: `playground_context.py`

**`build_playground_context`:** Bundles headlines, calendar events, ranked factors, cascade map for workbench triggers.

**Cache:** `playground_context_latest.json` keyed by doc `as_of`.

**Issues:**
- `live_fetch=False` in worker — headlines from doc unless explicit refresh
- Duplicate events from `upcoming_events` + `event_impact_curves`
- `cascade_calibration` rarely populated on doc

---

## File 4: `FactorImpactWorkbench.tsx`

- Debounced 300ms `POST /trade/index-prediction/simulate`
- Baseline: `artifact.prediction.expected_return_pct` (L51 area)
- Shock slider ±10%, cascade toggle
- Headline/event selection → `triggerToWorkbenchState`

**Correctness:** Does not re-run full pipeline — by design for interactivity. User must understand simulate ≠ Run analysis.

---

## File 5: `usePlaygroundContext.ts`

In-memory cache `ticker|asOf[:19]`; dedupes inflight fetches. No bust if playground file updated without new `as_of`.

---

## Phase 5 verdict

**Attribution is useful for direction of macro influence** if labeled as approximate under correlation. **Simulate baseline mismatch (N-05)** is the main user-facing inconsistency. Workbench is **not** a verification tool for the full hybrid+debate headline.

---

## Issues logged

| ID | Severity |
|----|----------|
| C4 | confirmed |
| C5 | confirmed |
| N-05 | high |
