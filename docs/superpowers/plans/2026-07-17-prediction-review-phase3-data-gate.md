# Prediction Review Phase 3 — Data Completeness Transparency

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or executing-plans.

**Goal:** Surface flow-coverage gate failures without blocking runs; down-weight macro Ridge when FII/DII/PCR coverage is below 90%.

**Architecture:** `data_quality_warning` on prediction dict; `macro_trust_multiplier=0.5` passed to `predict_nifty` when gate fails; UI amber banner.

**Tech Stack:** Python 3, pytest, React PredictionSummary, `data_completeness.py`, `predictor.py`.

## Global Constraints

- `macro_trust_multiplier=0.5` is fixed — not tuned on miss dates.
- Continue bottom-up + scenarios when gate fails.

---

## Task 1 — Macro trust multiplier

**File:** `predictor.py`

- [ ] Add `macro_trust_multiplier: float = 1.0` to `predict_nifty` and `_predict_macro_delta`
- [ ] Multiply `_macro_trust_weight(mae)` by multiplier

## Task 2 — Attach warning to prediction

**Files:** `aggregator.py`, `light_refresh.py`

- [ ] When `passes_gate` is false, set `prediction["data_quality_warning"]` with gate, min_pct, threshold, message
- [ ] Pass `macro_trust_multiplier=0.5` to `predict_nifty`
- [ ] Store completeness summary on doc meta (additive)

## Task 3 — UI banner

**Files:** `PredictionSummary.tsx`, `api.ts`

- [ ] Amber banner when `data_quality_warning` present
- [ ] TypeScript type for warning object

## Task 4 — Tests

- [ ] `tests/test_data_completeness.py` or predictor test: multiplier reduces macro delta
- [ ] `tests/test_index_aggregator.py`: warning on mocked gate fail

**Verify:**

```bash
python -m pytest tests/test_data_completeness.py tests/test_index_aggregator.py tests/test_index_predictor.py -q
cd vibetrading/frontend && npm run build
```
