# Prediction Algorithms — Per-Track Hardening

> Companion to [tracks catalog](2026-07-17-prediction-algorithms-tracks-catalog.md) and [master plan](2026-07-17-prediction-algorithms-master-plan.md).

**Goal:** Each forecast track wrapper matches its catalog spec, shares canonical forecast paths with backtest/live, and has regression tests.

**Status:** Implemented July 2026.

---

## Shared macro path

**Module:** [`macro_forecast.py`](../../../integrations/trade_integrations/dataflows/index_research/macro_forecast.py)

**Order (canonical, matches `predict_nifty`):**

```
predict_macro_delta_gated → merge_overlay_into_macro → shrink_macro_delta
```

Used by: `macro_only` track, `backtest_runner` macro-only eval rows.

---

## Track 1 — `quant_ridge`

| Item | Detail |
|------|--------|
| Spec | Pre-reconcile hybrid via `predict_nifty()` |
| Module | `tracks/quant_ridge.py` |
| Live | Aggregator passes `prediction_snapshot` with `pre_reconcile: true` |
| Walk-forward | Full `predict_nifty`; news features enriched in `build_track_context` |
| Tests | `test_track_quant_ridge_calls_predict_nifty`, snapshot provenance |

---

## Track 2 — `macro_only`

| Item | Detail |
|------|--------|
| Spec | Macro delta only — backtest parity |
| Module | `tracks/macro_only.py` → `compute_macro_only_return()` |
| Fix | `backtest_runner` overlay/shrink order + `day_str` bug |
| Tests | `test_macro_forecast_parity.py`, `test_parity_macro_only_matches_backtest` |

---

## Track 3 — `bottom_up`

| Item | Detail |
|------|--------|
| Spec | Diagnostic constituent rollup only |
| Module | `tracks/bottom_up.py` |
| Provenance | `signal_count`, `min_hybrid_constituents` (8) |
| Combiner | Reject `quant_ridge` + `bottom_up` presets |
| Tests | Archive parity vs `bottom_up_return_from_archives` |

---

## Track 4 — `scenario_anchor`

| Item | Detail |
|------|--------|
| Spec | `scenario_weighted_return_pct(scenarios, spot)` |
| Module | `tracks/scenario_anchor.py` |
| Tests | Independent of `predict_nifty`; weighted-return fixture |

---

## Track 5 — `event_overlay`

| Item | Detail |
|------|--------|
| Spec | Standalone `compute_event_overlay()` |
| Module | `tracks/event_overlay.py` |
| Fix | Always `available=true`; `method` in provenance when disabled |
| Tests | Active topic vs disabled calibration |

---

## Track 6 — `naive_zero` / `naive_momentum`

| Item | Detail |
|------|--------|
| Spec | Zero baseline; momentum from horizon-aligned return factor |
| Module | `tracks/naive_baselines.py` |
| Fix | `horizon.days ≤ 7` → `nifty_return_7d`, else `nifty_return_14d` |
| Tests | Horizon-aware factor selection |

---

## Track 7 — `debate_numeric`

| Item | Detail |
|------|--------|
| Spec | `extract_structured_debate()` — live only |
| Module | `tracks/debate_numeric.py` |
| Provenance | `backtest_eligible: false` |
| Tests | Mock debate JSON with `%` parse |

---

## Track 8 — `headline_legacy`

| Item | Detail |
|------|--------|
| Spec | Post-reconcile headline; live includes debate merge |
| Module | `tracks/headline_legacy.py` |
| WF replay | `legacy_replay.py` sets `debate_merged=false` |
| Tests | Legacy differs from quant when reconcile shifts forecast |

---

## Metadata — `ForecastTrack`

| Field | Source |
|-------|--------|
| `backtest_eligible` | `TRACK_BACKTEST_ELIGIBLE` in registry |
| Promotion | Only `backtest_eligible` tracks in combiner scoring |

---

## Deferred (Phase G)

- `quant_ridge_no_overlay`
- Debate history archive for walk-forward
- `cause_simulation` registry wrapper
