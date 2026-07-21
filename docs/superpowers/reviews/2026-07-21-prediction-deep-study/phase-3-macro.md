# Phase 3 — Macro Model Core

**Goal:** Ridge training, inference, gates — source of `macro_delta_pct`.

**Research backing:**
- Ridge (L2) stabilizes multicollinearity; **must scale features** ([IBKR Quant](https://www.interactivebrokers.com/campus/ibkr-quant-news/beyond-the-straight-line-advanced-linear-regression-models-for-financial-data/))
- Time series: **walk-forward**, not random k-fold ([DCF Modeling](https://www.dcfmodeling.com/blogs/blog/leveraging-regression-financial-model))
- Short-horizon index direction ~50% with linear macro is **expected** ([rolling Ridge limits](https://wire.insiderfinance.io/adaptive-time-series-forecasting-with-rolling-ridge-regression-a82f4a718471))

---

## File 1: `factor_matrix.py`

**52 canonical keys** (`MACRO_FACTOR_KEYS` L13–66): macro, flows, technicals, Phase I valuation/spreads, calendar flags.

**Training pipeline:**

```269:357:integrations/trade_integrations/dataflows/index_research/factor_matrix.py
def build_factor_matrix(history_df, horizon):
    # Target y = forward Nifty return over horizon.days
    # Select columns via _select_macro_columns (horizon order, corr filter, news ridge)
    # _EXCLUDED_REDUNDANT drops oil_wti, constituent_momentum_7d, etc.
    # Max 40 features before poly expansion
```

**Poly degree 2** in predictor → interaction terms. With ~365 rows and 40 features, interaction count is large — Ridge α=50 mitigates overfit but **C10** small-n remains.

**C7:** Full key list is 52; frontend checks only 24 (Phase 7).

---

## File 2: `calibrator.py`

- `ensure_ridge_model_artifact()` — load or retrain if feature universe drifted
- `should_retrain()` — rolling MAE >20% vs baseline
- Walk-forward MAE, R², direction hit rate stored on artifact

**Issue:** `artifact_needs_retrain` returns False on empty history — keeps stale artifact if data missing.

---

## File 3: `predictor.py`

### Hybrid headline

```600:613:integrations/trade_integrations/dataflows/index_research/predictor.py
def predict_nifty(spot, signals, macro_factors, horizon, *, model_artifact, scenario_anchor_return_pct, macro_trust_multiplier=1.0, ...):
    """Hybrid forecast: bottom-up constituent attribution + macro Ridge delta."""
```

### Macro delta path

1. `enrich_macro_with_news_features` (C3)
2. `_predict_macro_delta` — Ridge on scaled poly features × `macro_trust_multiplier`
3. `predict_macro_delta_gated` — regime block weights on inputs
4. **N-02:** If `abs(gated_raw) <= 1e-9`, **ungated** macro kept (L652–653)
5. `compute_event_overlay` + `merge_overlay_into_macro` (C3 double-count risk)
6. `shrink_macro_delta(scenario_anchor)` then `cap_macro_delta(±5%)`
7. `expected = bottom_up + macro_delta`
8. `apply_sign_conflict_gate` on direction vs scenario anchor

### Shrink on sign conflict (macro vs anchor)

```50:66:integrations/trade_integrations/dataflows/index_research/predictor.py
def shrink_macro_delta(raw_macro, scenario_anchor_return_pct=None):
    if raw_macro * scenario_anchor_return_pct < 0:
        # blend toward anchor before cap
```

### Sign conflict gate

```512:526:integrations/trade_integrations/dataflows/index_research/predictor.py
def apply_sign_conflict_gate(...):
    # On macro vs anchor sign conflict: direction_view = neutral, confidence halved
```

**N-03:** Gate uses pre-overlay `raw_macro`; shrink uses post-overlay macro.

**C8:** `macro_trust_multiplier` from flow gate correctly passed to `_predict_macro_delta`.

---

## File 4: `regime_gates.py`

- `resolve_regime_label` — high_fear / trend_down / range_bound
- `block_gate_weights` — e.g. trend_down zeros flow block
- `predict_macro_delta_gated` — multiply factor blocks before poly expansion

**Issue:** `trend_down` zeros all flows — may discard contrarian FII signal; `FII_CONTRARIAN_FACTORS` only lists `fii_net_5d`.

---

## File 5: `event_overlay.py`

- `compute_event_overlay` — calibrated topic shocks, cap ±2%
- `merge_overlay_into_macro` — adds to macro delta **after** Ridge
- `enrich_macro_with_news_features` — panel/hub news columns for live day

**C3 confirmed:** Ridge may already include `news_*` in training; overlay adds separate shock at inference → double-count risk when both active.

---

## Model artifact (stored)

Path: `_data/index_factors/model/latest.json`

Fields: `coefficients`, `intercept`, `mae`, `r2_walk_forward`, `feature_names`, `feature_means/stds`, direction logistic coeffs, `direction_hit_rate_oos`.

---

## Phase 3 verdict

**Architecturally sound** for an inspectable macro overlay: Ridge + scaling + walk-forward + caps. Main code risks: **gate bypass (N-02)**, **overlay ordering (N-03)**, **news double-count (C3)**, **small n (C10)**.

---

## Issues logged

| ID | Severity |
|----|----------|
| N-02 | high |
| N-03 | medium |
| C3 | confirmed |
| C7 | confirmed |
| C8 | confirmed (wiring) |
| C10 | confirmed |
