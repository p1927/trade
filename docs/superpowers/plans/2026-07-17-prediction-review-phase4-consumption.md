# Prediction Review Phase 4 — Consumption Layer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or executing-plans.

**Goal:** Fix thesis-break and hub consumption paths that misread options view strings and stock/index return fields.

**Architecture:** Normalize prediction views for adverse-scenario logic; fallback to `expected_return_pct`; extend `prediction_action` for options archetypes.

**Tech Stack:** Python 3, pytest, `monitor/thesis_break.py`, `hub_bridge.py`.

## Global Constraints

- No cross-asset schema rename in this phase (`range.confidence` deferred).

---

## Task 1 — `thesis_break` normalization

**File:** `integrations/trade_integrations/monitor/thesis_break.py`

- [ ] Add `_normalize_prediction_view(view: str) -> str`
- [ ] Use in `_is_adverse_scenario` and `_prediction_view`
- [ ] `_expected_move_pct`: fallback to `abs(expected_return_pct)` when move fields absent

## Task 2 — `prediction_action` archetypes

**File:** `vibetrading/agent/src/trade/hub_bridge.py`

- [ ] Views containing `bull` (not `bear`) → buy; containing `bear` → sell; else hold

## Task 3 — Options `_prediction_view` tests

**File:** `tests/test_options_prediction_view.py` (new)

- [ ] Cover heuristic matrix from `options_research/aggregator.py::_prediction_view`

## Task 4 — Tests

- [ ] `tests/test_thesis_break.py`: `bullish_earnings` adverse scenario; stock `expected_return_pct` move breach

**Verify:**

```bash
python -m pytest tests/test_thesis_break.py tests/test_options_prediction_view.py -q
```
