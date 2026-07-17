# Prediction Algorithms — Tracks & Combiners Catalog

> Companion to [master plan](2026-07-17-prediction-algorithms-master-plan.md).

**Goal:** Single reference for every forecast track, combiner, factors, modules, backtest status, and valid merge sets.

**Last updated:** July 2026 (reflects shipped lab + scoreboard UI)

---

## Track summary

| Track ID | Type | Live | Walk-forward v1 | `backtest_eligible` | In default combiner? | Primary module |
|----------|------|------|-----------------|----------------------|----------------------|----------------|
| `quant_ridge` | Hybrid quant | Yes | Yes | true | Control / passthrough | `predictor.predict_nifty()` |
| `macro_only` | Quant (macro path) | Yes | Yes | true | Parity only | `regime_gates` + `event_overlay` |
| `quant_ridge_no_overlay` | Quant split | No | No | — | Attribution experiments | **Phase G** — not shipped |
| `bottom_up` | Constituent | Yes | No | false | **Diagnostic only** | `attribution.py` |
| `scenario_anchor` | Event / outside view | Yes | Yes | true | Yes | `scenarios.py` |
| `event_overlay` | News shock | Yes | Yes | true | Yes (high stress) | `event_overlay.py` |
| `naive_zero` | Baseline | Yes | Yes | true | No | constant |
| `naive_momentum` | Baseline | Yes | Yes | true | No | factor snapshot |
| `debate_numeric` | Inside view (LLM) | Yes | No | false | No (v1) | `debate_synthesis.py` |
| `headline_legacy` | Combiner A/B | Yes | No | false | A/B only | reconcile + debate |
| `cause_simulation` | What-if | Via `/simulate` | No | — | No | **Not in lab registry** — `prediction_counterfactual.py` |

**Walk-forward runner** (`evaluator/walk_forward.py`) evaluates the six core tracks + five combiners on nested OOS dates. Live aggregator runs all nine registry tracks via `run_all_tracks()`.

## Cause → channel → track mapping

| Real-world cause | News topic / tag | Channel factors | Primary track(s) |
|------------------|------------------|-----------------|------------------|
| Geopolitical war / Iran | `war`, `news_war_7d` | `oil_brent`, `gold`, `india_vix`, `news_war_7d` | `event_overlay`, `scenario_anchor` |
| Oil supply shock | `oil`, `news_oil_7d` | `oil_brent`, `oil_wti`, `usd_inr` | `event_overlay`, `quant_ridge` |
| Fed / US rates | `us_markets`, `news_fii_7d` | `us_10y`, `sp500`, `usd_inr` | `quant_ridge`, `macro_only` |
| US fiscal / debt | (macro narrative) | `us_10y`, `sp500`, `usd_inr`, `fii_net_5d` | `quant_ridge` |
| RBI policy | `rbi`, `news_rbi_7d` | `repo_rate`, `india_vix`, `cpi_yoy_proxy` | `scenario_anchor`, `event_overlay` |
| FII selling / flows | `fii`, `news_fii_7d` | `fii_net_5d`, `fii_net_5d_momentum`, `dii_net_5d` | `quant_ridge`, `bottom_up` |
| Valuation stretch | earnings season, P/E narrative | `nifty_earnings_yield`, `equity_risk_premium`, `nifty_pb_zscore_5y` | `quant_ridge`, `macro_only` |
| Credit stress | widening spreads news | `india_credit_spread`, `india_term_spread` | `quant_ridge`, `scenario_anchor` |
| INR depreciation | FX headlines | `usd_inr`, `usd_inr_momentum_5d` | `quant_ridge`, `macro_only` |
| VIX shock | fear spike | `india_vix`, `india_vix_velocity_3d` | `quant_ridge`, `event_overlay` |
| Earnings cluster | calendar events | constituent events, `is_results_season` | `scenario_anchor`, `bottom_up` |
| Union budget | `is_budget_week` | calendar + macro | `scenario_anchor` |
| Monthly expiry | `days_to_monthly_expiry` | calendar + vol | `scenario_anchor`, `quant_ridge` |
| Constituent sentiment | FinBERT | `index_sentiment` | `bottom_up`, `quant_ridge` |

---

## Track specifications

### `quant_ridge` (canonical)

**Output:** `expected_return_pct`, `view`, `bottom_up_return_pct`, `macro_delta_pct`, direction fields.

**Formula:**
```
expected_return_pct = bottom_up + shrink_macro_delta(raw_macro_with_overlay, scenario_anchor)
```

**Factors (`MACRO_FACTOR_KEYS` from [`factor_matrix.py`](../../../integrations/trade_integrations/dataflows/index_research/factor_matrix.py)):**

| Group | Keys |
|-------|------|
| Global | `oil_brent`, `oil_wti`, `usd_inr`, `usd_inr_momentum_5d`, `gold`, `sp500`, `us_10y`, `us_10y_velocity_3d` |
| India flows | `fii_net_5d`, `fii_net_5d_momentum`, `dii_net_5d`, `institutional_net_5d`, `dii_absorption_ratio` |
| Vol / deriv | `india_vix`, `india_vix_velocity_3d`, `nifty_pcr`, `fii_fut_long_short_ratio`, `qfinindia_*` |
| Valuation / structural floor | `nifty_pe`, `nifty_earnings_yield`, `nifty_dividend_yield`, `nifty_pb`, `nifty_book_to_market`, `nifty_pb_zscore_5y`, `equity_risk_premium` |
| Liquidity spreads | `india_10y`, `india_91d_tbill`, `india_term_spread`, `india_credit_spread`, `repo_rate`, `cpi_yoy_proxy` |
| Technical | `nifty_return_7d/14d`, RSI, MA distances, MACD, BB, stoch, ADX, ATR, golden cross |
| Sentiment | `index_sentiment` |
| Alpha Zoo | `alpha_zoo_*` (if promoted) |
| Calendar | `days_to_monthly_expiry`, `is_budget_week`, `is_results_season` |
| News (ridge + overlay) | `news_material_7d`, `news_war_7d`, `news_oil_7d`, `news_fii_7d`, `news_rbi_7d`, … |

Keys marked **Planned** in master plan Phase I — present in catalog for ablation; omitted from live Ridge until backfill + gate pass.

### Standard predictor reference (literature → factor key)

| Category | Predictor | Factor key(s) | 14d signal |
|----------|-----------|---------------|------------|
| Valuation | Earnings yield E/P | `nifty_earnings_yield` | E/P below bond yield → stagnation / down |
| Valuation | Dividend yield D/P | `nifty_dividend_yield` | Spike → floor / long regime |
| Valuation | Book-to-market B/M | `nifty_book_to_market`, `nifty_pb_zscore_5y` | Extreme P/B vs 5y mean → reversal |
| Liquidity | Term spread | `india_term_spread` | Steepening bullish; invert bearish |
| Liquidity | Credit / default spread | `india_credit_spread` | Widening → leading equity stress |
| Liquidity | FII net flows | `fii_net_5d`, `fii_net_5d_momentum` | Strong short-term directional (India) |
| Vol regime | VIX velocity | `india_vix_velocity_3d` | Spike → negative 14d power |
| Vol regime | Equity risk premium | `equity_risk_premium` | Positive → equity inflow; negative → debt rotation |
| Global | USD/INR momentum | `usd_inr_momentum_5d` | INR depreciation → negative Nifty |
| Global | US 10Y | `us_10y`, `us_10y_velocity_3d` | Spike → EM outflow |

**Redundancy rules (`factor_matrix.py`):**

| Keep | Drop when both present |
|------|------------------------|
| `nifty_earnings_yield` | `nifty_pe` |
| `nifty_book_to_market` | `nifty_pb` (if corr > 0.95) |
| `india_term_spread` | raw `india_10y` + `india_91d_tbill` (pair kept for attribution only) |

**Tools:** sklearn Ridge + PolynomialFeatures, `train_macro_ridge`, `regime_gates`, `event_overlay`, `attribution`.

**Wrapper:** `prediction_algorithms/tracks/quant_ridge.py` → calls `predict_nifty()` before reconcile/debate.

---

### `macro_only`

**Output:** macro delta path only (matches [`backtest_runner.py`](../../../integrations/trade_integrations/dataflows/index_research/backtest_runner.py)).

**Steps:** `train_macro_ridge` → `predict_macro_delta_gated` → `merge_overlay_into_macro` → `shrink_macro_delta`.

**Use:** Backtest parity; combiner preset with `scenario_anchor` (avoids bottom-up double-count).

---

### `bottom_up` (diagnostic)

**Formula:** `rollup_attribution(attribute_constituents(signals))`.

| Input | Rule |
|-------|------|
| Sentiment | 70% × `_SENTIMENT_BETA=5` |
| Momentum 7d | 30% × `_MOMENTUM_SCALE=0.5` when present |
| Weight | NIFTY 50 index weight |
| Earnings | +0.5% bump in horizon |

**Backtest:** `_bottom_up_from_archives()` when `company_research/history/{date}.json` exists.

**Never combine with `quant_ridge`** (bottom-up already inside quant).

---

### `scenario_anchor`

**Formula:** `scenario_weighted_return_pct(scenarios, spot)`.

**Inputs:** earnings cluster count, RBI/budget/expiry scenarios, macro drift rows from `build_index_scenarios()`.

**Cause role:** Outside-view / event-table forecast (superforecasting base rate).

---

### `event_overlay`

**Formula:** Sum of calibrated topic shocks when active (`news_shock_calibration.json` × topic intensity).

**Topics:** war, oil, fii, rbi, us_markets.

**Split from quant in Phase G** for lab scoring; today overlay is inside `predict_nifty`.

**Library:** numpy + existing calibration — no new dep.

---

### `naive_zero` / `naive_momentum`

| Track | Rule |
|-------|------|
| `naive_zero` | `expected_return_pct = 0` |
| `naive_momentum` | `expected_return_pct = nifty_return_7d` or `14d` |

Sanity baselines on scoreboard.

---

### `debate_numeric`

**Source:** `{TICKER}/agent_debate/latest.json`.

**Extraction:** `extract_structured_debate()` — rating, parsed `%`, view from text.

**Backtest:** unavailable v1 → `available=false` in backtest rows.

---

## Combiner catalog

| Combiner ID | Formula | Track set | Walk-forward backtest | Params (nested WF) | Library |
|-------------|---------|-----------|----------------------|---------------------|---------|
| `quant_only` | `y = y_quant` | quant_ridge | Yes | — | numpy |
| `equal_weight_2` | mean | macro_only + scenario_anchor | Yes | — | numpy |
| `equal_weight_3` | mean | macro_only + scenario + event_overlay | Yes | — | numpy |
| `inverse_mae_w6` | inverse MAE weights | configurable | Live / manual | W=6 | numpy |
| `inverse_mae_w12` | inverse MAE weights | configurable | Live / manual | W=12 | numpy |
| `shrinkage_50` | 0.5·w_opt + 0.5·w_equal | on inverse_mae | Yes | λ=0.5 | numpy |
| `alignment_grid` | λ·y_quant + (1-λ)·y_scenario | quant + scenario | Live / manual | λ ∈ grid | numpy |
| `stress_conditional` | if cause_stress≥60: weight scenario+overlay else quant | H1 | Yes | thresholds | numpy |
| `fixed_legacy` | reconcile 25/75 + debate 60/40 | pipeline replay | Live only | — | existing |

### Combiner math (implement in `combiners/_math.py`)

**Equal weight:** `ŷ = (1/|K|) Σ y_k` over available tracks.

**Inverse MAE** at eval date t, window W, ε=0.01:
```
w_k(t) = (1/max(ε, MAE_k(t-W:t-1))) / Σ_j (...)
ŷ(t) = Σ_k w_k(t) · y_k(t)
```

**Shrinkage:** `w(t) = λ·w_opt(t) + (1-λ)·w_equal` — λ chosen on prior eval rows only.

**Alignment:** `ŷ = λ·y_quant + (1-λ)·y_scenario` — λ from prior eval rows only.

---

## Valid combiner track sets

| Preset | Tracks | Notes |
|--------|--------|-------|
| `core` | macro_only, scenario_anchor | No double-count |
| `core_plus_news` | macro_only, scenario_anchor, event_overlay | High cause_stress regimes |
| `full_diagnostic` | all tracks logged | Scoreboard only; not for merge |
| **Invalid** | quant_ridge + bottom_up | Double-count |
| **Invalid** | quant_ridge + event_overlay | If overlay already in quant (unless quant_ridge_no_overlay) |

---

## Folder layout (shipped)

```
prediction_algorithms/
  types.py              # TrackContext, ForecastTrack, ForecastLabResult
  config.py             # lab_enabled, lab_mode, default_combiner_id
  api.py                # run_forecast_lab (single entry)
  context_builder.py    # build_track_context from hub snapshot
  registry.py           # TRACK_REGISTRY, TRACK_BACKTEST_ELIGIBLE
  promotion.py          # evaluate_promotion, enrich_scoreboard_with_live
  tracks/               # one file per track (wrappers only)
  combiners/            # _math.py + combiner implementations
  evaluator/
    walk_forward.py     # nested OOS per track + combiner
    scoreboard.py       # summarize_track_metrics, save/load JSON
    chart_series.py     # build_track_chart_payload for UI
  causes/               # cause_stress_index.py, channel_attribution.py

scripts/run_track_backtest.py   # CLI recompute scoreboard

vibetrading/frontend/
  pages/Prediction.tsx                    # Track Scoreboard tab
  components/prediction/
    TrackScoreboardPanel.tsx
    TrackScoreboardReplaySection.tsx      # single + compare replay
  components/charts/
    NiftyForecastReplayChart.tsx          # extended forecastIndex prop
    MultiTrackForecastReplayChart.tsx
    TrackScoreboardChart.tsx
  lib/trackScoreboardReplayUtils.ts
```

## Tests (per track)

| Test | Asserts | Status |
|------|---------|--------|
| `test_track_quant_ridge_calls_predict_nifty` | spy on canonical function | **Shipped** |
| `test_track_scenario_anchor_independent` | no predict_nifty call | **Shipped** |
| `test_combiner_no_double_count` | invalid presets rejected | **Shipped** |
| `test_inverse_mae_no_lookahead` | weights use t-W:t-1 only | **Shipped** |
| `test_parity_macro_only_matches_backtest` | MAE within tolerance | **Shipped** |
| `test_fundamental_features.py` | Phase I valuation derivations | **Shipped** |
| `test_spread_features.py` | Phase I velocity / spread derivations | **Shipped** |
| `test_phase_i_coverage.py` | coverage gate for Ridge inclusion | **Shipped** |
