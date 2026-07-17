# Prediction Review Phase 2 — Index Debate Hybrid Merge

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or executing-plans.

**Goal:** Align `merge_index_prediction` with stock hybrid merge — blend magnitude, recompute views, provenance blocks.

**Architecture:** 60/40 debate/quant on `expected_return_pct` when debate parses a return; recompute `view` + `direction_view`; second `finalize_index_prediction` after merge in orchestrators.

**Tech Stack:** Python 3, pytest, `research/debate_synthesis.py`, orchestrators.

## Global Constraints

- Phase 1 pipeline order and finalize helper must be in place first.
- Do not change debate extraction regex/keywords in this phase.

---

## Task 1 — Shared view classifier (if import cycle)

**File:** `integrations/trade_integrations/dataflows/index_research/views.py` (new, optional)

- [ ] Extract `classify_index_view(expected_return_pct)` from predictor thresholds (±0.3%)
- [ ] Use from predictor, scenarios, debate_synthesis

## Task 2 — Refactor `merge_index_prediction`

**File:** `integrations/trade_integrations/research/debate_synthesis.py`

- [ ] Blend `expected_return_pct`: `0.6 * debate + 0.4 * quant` when debate has parsed return
- [ ] Recompute `view` and `direction_view` from blended return
- [ ] Confidence: `min(debate_conf, quant direction_confidence)` when both present
- [ ] Add `provenance`, `debate`, `quant` sub-blocks like stock merge
- [ ] Preserve `debate_rationale`, `debate_as_of`

## Task 3 — Orchestrator finalize pass

**Files:** `aggregator.py`, `light_refresh.py`

- [ ] After `merge_index_prediction`, call `finalize_index_prediction` again

## Task 4 — Tests

- [ ] Update `tests/test_debate_synthesis.py::test_merge_index_prediction`
- [ ] Add case: debate bearish + quant bullish +1.2% → consistent views and blended return

**Verify:**

```bash
python -m pytest tests/test_debate_synthesis.py tests/test_index_aggregator.py -q
```
