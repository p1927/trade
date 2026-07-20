# Prediction Algorithms Phase G Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Phase G — split overlay tracks for fair combiner scoring, debate archive walk-forward contract, and gated LightGBM experiment track.

**Architecture:** Overlay split tracks (`quant_ridge_no_overlay`, `macro_only_no_overlay`) already exist; Phase G wires a fair quant combiner preset, completes debate archive eligibility + promotion rule 6, and adds an experimental LightGBM track gated by `ml_experiments_defer.py` (not combiner merge v1).

**Tech Stack:** Python forecast lab, hub JSON archive, numpy combiners, optional `lightgbm`, React scoreboard UI.

## Global Constraints

- Reuse existing Ridge/overlay math via track wrappers — no duplicated predictors.
- Combiners: numpy only in v1 — no sktime as merge engine.
- `debate_numeric` excluded from auto-promotion until ≥60 dated `agent_debate/history/*.json` files.
- LightGBM is experiment-only — not in default walk-forward or combiner merge.
- Do not edit `2026-07-17-prediction-algorithms-master-plan.md` status table unless explicitly asked.

---

### Task 1: Fair quant combiner preset

**Files:**
- Modify: `prediction_algorithms/track_constants.py`
- Modify: `prediction_algorithms/combiners/__init__.py`
- Modify: `prediction_algorithms/evaluator/chart_series.py`

- [ ] Add `COMBINER_QUANT_THREE_TRACK_IDS = (quant_ridge_no_overlay, scenario_anchor, event_overlay)`
- [ ] Register `equal_weight_quant_3` in `BACKTEST_COMBINER_IDS` and `COMBINER_REGISTRY`
- [ ] Add chart label for new combiner

**Verify:** `pytest tests/test_prediction_algorithms_tracks.py::test_equal_weight_quant_3_uses_split_quant_track -q`

---

### Task 2: Debate archive contract

**Files:**
- Modify: `prediction_algorithms/tracks/debate_numeric.py`
- Modify: `prediction_algorithms/track_constants.py` (implementation notes)
- Modify: `prediction_algorithms/promotion.py` (rule 6 metadata)
- Create: `scripts/archive_agent_debate_date.py` (copy payload to history date — no agent re-run)

- [ ] `debate_numeric.backtest_eligible` reflects `debate_backtest_eligible(ticker)` when payload present
- [ ] Promotion output includes `debate_archive_eligible` and `debate_numeric_promotion_blocked`
- [ ] Archive script: `--ticker`, `--date`, optional `--from-file`

**Verify:** `pytest tests/test_prediction_algorithms_tracks.py -k debate -q`

---

### Task 3: LightGBM gated experiment track

**Files:**
- Modify: `prediction_algorithms/ml_experiments_defer.py`
- Create: `prediction_algorithms/tracks/lightgbm_macro.py`
- Modify: `prediction_algorithms/registry.py`

- [ ] `resolve_direction_oos_pct(ticker)` reads scoreboard or env fallback
- [ ] `lightgbm_macro` track: deferred when Phase 3 gate passes; trains when ungated + `lightgbm` importable
- [ ] Experimental tracks only when `INDEX_PREDICTION_EXPERIMENTAL_TRACKS=1`

**Verify:** `pytest tests/test_prediction_algorithms_tracks.py -k lightgbm -q`

---

### Task 4: UI + hub tests

**Files:**
- Modify: `vibetrading/frontend/src/lib/trackScoreboardReplayUtils.ts`
- Modify: `vibetrading/frontend/src/lib/trackScoreboardUtils.ts`
- Modify: `tests/test_prediction_algorithms_tracks.py`

- [ ] Add `macro_only_no_overlay` to frontend canonical list + labels/colors
- [ ] Tests for `walk_forward_track_ids`, hub debate history roundtrip

**Verify:** `pytest tests/test_prediction_algorithms_tracks.py tests/test_prediction_algorithms_combiners.py -q`

---

### Task 5: End-to-end verification

- [ ] `python scripts/run_track_backtest.py --ticker NIFTY --days 60 --eval-step 5` (smoke)
- [ ] Update `.superpowers/sdd/progress.md`
