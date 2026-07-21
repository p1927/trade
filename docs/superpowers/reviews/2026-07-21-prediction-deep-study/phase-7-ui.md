# Phase 7 — UI Consumption & Verification

**Goal:** Every visible number traces to an artifact field; audit panel accuracy.

---

## File 1: `Prediction.tsx` — section order

When `artifact` present (analysis mode):

| Order | Section | Feeds forecast? |
|-------|---------|-----------------|
| 1 | PredictionControls | trigger only |
| 2 | PredictionVerificationPanel | audit only |
| 3 | PredictionSummary, CauseAttribution, TechnicalContextStrip, NiftyForecastReplayChart | display |
| 4 | FactorImpactWorkbench | simulate overlay |
| 5 | ScenarioTiles | display (scenarios fed reconcile) |
| 6 | ConstituentDrivers | display (fed bottom-up) |
| 7 | FactorCompositionTable, DerivativesFactorsPanel | display |
| 8 | IndexFactorTimelineChart, SectorBreadth, MarketContext | display |
| 9 | EquationCard | display equation ref |
| 10 | CausalFactorExplorer | simulate |
| 11 | NewsImpactPanel | overlay context |
| 12 | ForecastHistorySection, IndexFactorLedgerPanel | track record |
| 13 | BacktestEvaluationPanel, MissAnalysis, QuantReview, Learning | verification |

**Note:** Page calls `loadBacktest(false)` on mount even before artifact — extra API load.

---

## File 2: `PredictionSummary.tsx`

Displays:
- `prediction.expected_return_pct`, `view`, `range`
- `direction_confidence`, `sign_conflict` banner
- Reconcile banner when `reconciled_with_scenarios`
- Accuracy: `direction_eval_count` from prediction or `accuracy.eval_count`

**N-12:** Subtitle switches between "walk-forward eval rows" and "reconciled" based on counts — mixed semantics when both exist.

**Missing:** Explicit badge when `debate_merged` (N-13) — user may not know headline ≠ bottom_up + macro.

---

## File 3: `EquationCard.tsx`

Shows `prediction.equation` (coefficients, intercept, R² walk-forward), decomposition:

```
expected ≈ bottom_up + macro_delta [+ event_overlay]
```

**Issue:** `TERM_LABELS` only 10 friendly names; expanded poly terms show raw feature strings.

When debate merged, equation still describes **quant leg** — may not match displayed expected return.

---

## File 4: `predictionVerification.ts`

**N-01 / C7 — key sync:**

Frontend `MACRO_MODEL_KEYS` (24 keys):

```4:29:vibetrading/frontend/src/lib/predictionVerification.ts
export const MACRO_MODEL_KEYS = [
  "oil_brent", "oil_wti", "usd_inr", ... "is_results_season",
] as const;
```

Backend `MACRO_FACTOR_KEYS` (52 keys) adds: TA stack (MACD, Bollinger, ADX, …), qfinindia_*, Phase I (ERP, term spread, credit spread, velocities, …).

**Comment L3 says "must match factor_matrix.py" — incorrect.** All 24 ⊆ backend, but **28 backend keys never audited in UI**.

**Impact:** `auditPredictionUi` reports false "missing macro" warnings when Ridge artifact uses Phase I columns that aren't in the 24-key list. Conversely, passes when only core 24 populated but TA columns empty.

**`ModelRole` types:** `feeds` | `display` | `context` | `verify` | `ops` — good separation for north star.

---

## File 5: `api.ts` — `IndexPredictionArtifact`

Typed fields mirror `_index_doc_to_panel` output: `prediction`, `factor_explanation`, `global_factors`, `pipeline_log`, `cascade_calibration`, etc.

Types are comprehensive; runtime JSON may include extra keys from forecast lab.

---

## Phase 7 verdict

UI is **structured and mostly honest** — equation card, verification panel, backtest scope banners. Gaps:
1. **MACRO_MODEL_KEYS mismatch (N-01)**
2. **Debate/reconcile headline not visually distinguished (N-13)**
3. **Accuracy label ambiguity (N-12)**

---

## Issues logged

| ID | Severity |
|----|----------|
| N-01 | medium |
| N-12 | medium |
| N-13 | low |
| C7 | confirmed |

---

## Study index

| Doc | Topic |
|-----|-------|
| [00-verification-playbook.md](00-verification-playbook.md) | Commands + cross-checks |
| [00-issue-register.md](00-issue-register.md) | All findings |
| [phase-0-e2e.md](phase-0-e2e.md) | Run → hub |
| [phase-1-data.md](phase-1-data.md) | Panel ingest |
| [phase-2-bottom-up.md](phase-2-bottom-up.md) | Constituents |
| [phase-3-macro.md](phase-3-macro.md) | Ridge hybrid |
| [phase-4-scenarios.md](phase-4-scenarios.md) | Scenarios/debate |
| [phase-5-explain-sim.md](phase-5-explain-sim.md) | Workbench |
| [phase-6-backtest.md](phase-6-backtest.md) | OOS eval |
| [phase-7-ui.md](phase-7-ui.md) | This doc |
