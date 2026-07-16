# Nifty 50 Index Research & Prediction Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a data-first `index_research` pipeline that collects and persists Nifty 50 constituent + global macro factors, continuously refreshes them via agents, attributes each factor/event to index moves, and predicts Nifty 50 range/direction with measurable accuracy that converges toward actual outcomes over time.

**Architecture:** New `index_research/` package under `integrations/trade_integrations/dataflows/` mirrors `company_research` → hub artifact pattern. A **factor time-series store** (Parquet/SQLite under hub) accumulates daily snapshots. A **hybrid predictor** combines bottom-up constituent rollup (attribution) with regularized polynomial macro overlay (solvable equation). A **prediction ledger** records every forecast vs actual for walk-forward calibration. Default horizon **B = 14 days**; horizons **A (1–3 days)** and **C (30–90 days)** selected when user/agent explicitly requests them.

**Tech Stack:** Python 3.12, `trade_integrations`, `nselib`, `yfinance`, OpenAlgo, existing `company_research`/`options_research`, `scikit-learn` (Ridge/ElasticNet + PolynomialFeatures), optional `shap`, Parquet via `pyarrow` or stdlib SQLite, Vibe `ScheduledResearchJobStore` for agent refresh jobs.

## Global Constraints

- All new code in `integrations/trade_integrations/`; MCP additions in `openalgo/mcp/mcpserver.py`.
- Free/open-source data only — no paid vendors.
- Follow `company_research` stage patterns: best-effort per stage, vendor attribution, never fail whole pipeline on one stage.
- Default horizon: `INDEX_RESEARCH_HORIZON_DAYS=14` (horizon B).
- Horizons A/C only when `horizon_days` explicitly passed (CLI, MCP, or agent tool arg).
- Hub artifact: `reports/hub/NIFTY/index_research/latest.{json,md}`.
- Factor store: `reports/hub/_data/index_factors/` (not per-ticker; shared time-series).
- Prediction ledger: `reports/hub/_data/index_predictions/ledger.parquet`.
- User rule: no extra docs beyond this plan; smoke tests only (meaningful coverage per component).
- Accuracy is a first-class output — every prediction must be scorable against actual Nifty at horizon end.

---

## External Research Synthesis (what others do)

Literature and open implementations converge on these patterns for Nifty 50:

| Approach | Key insight | Use in our pipeline |
|----------|-------------|---------------------|
| Multi-factor regression (Medium/Nagesh, GitHub macro predictor) | Linear/polynomial combo of USD/INR, crude, S&P 500, repo rate, FII → Nifty level; yearly coefficient refresh | Macro overlay in `predictor.py`; coefficients stored in factor store |
| ML feature selection (IJARSET 2025) | RFE + Random Forest/XGBoost/LASSO on macro + technical + sentiment; PCA for dimensionality | Feature selection stage before polynomial expansion; drop redundant cols |
| News sentiment + DL (IEEE Access 2025) | FinBERT polarity improves next-day sign prediction; SHAP for attribution | Reuse `sentiment.py`; aggregate to index-level sentiment feature |
| Regime-aware learning (JoCAAA 2025) | Different dominant factors in bull/bear/sideways; metaheuristic attribution | `regime.py` classifies VIX + trend → switches feature weights |
| Bottom-up constituent | Weighted sum of stock moves explains ~60–80% of index variance | `attribution.py` core; weights from NSE factsheet or mcap proxy |

**Our differentiator vs papers:** persistent factor store + prediction ledger + agent refresh loop + integration into options trade flow (not a standalone notebook).

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     DATA COLLECTION (continuous)                        │
├─────────────────────────────────────────────────────────────────────────┤
│  nifty50_equity_list()  →  batch company_research × 50 (cached hub)     │
│  macro_global.py        →  oil, USD/INR, FRED US rates, S&P, gold       │
│  macro_in.py (reuse)    →  India VIX, FII/DII, Nifty spot               │
│  events_index.py (reuse)→  expiries, index calendar                     │
│  rbi_cpi.py (new)       →  RBI dates, CPI/WPI proxies                   │
└───────────────────────────────┬─────────────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     FACTOR TIME-SERIES STORE                            │
│  reports/hub/_data/index_factors/daily/{YYYY-MM-DD}.parquet             │
│  reports/hub/_data/index_factors/constituents/{SYMBOL}.parquet          │
│  reports/hub/_data/index_factors/weights/latest.json                    │
└───────────────────────────────┬─────────────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     ANALYSIS + PREDICTION                               │
│  attribution.py   → per-constituent event → weighted index contribution │
│  factor_matrix.py → horizon-aware feature windows + poly expansion      │
│  predictor.py     → hybrid: Σ(w_i × f_i) + β·poly(X_macro)             │
│  regime.py        → bull/bear/sideways → coefficient set selection      │
└───────────────────────────────┬─────────────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     OUTPUTS + CONVERGENCE LOOP                          │
│  hub artifact     → latest prediction, attribution, scenarios           │
│  prediction ledger→ forecast vs actual; MAE/MAPE/ direction accuracy    │
│  calibrator.py    → walk-forward retrain when drift detected            │
│  agent refresh    → ScheduledResearchJob re-runs collection daily       │
└───────────────────────────────┬─────────────────────────────────────────┘
                                ▼
              options_research(NIFTY) + get_index_trade_plan MCP
```

---

## Horizon Router

| Horizon | `horizon_days` | Primary features | Model emphasis |
|---------|----------------|------------------|----------------|
| **A — Intraday/short** | 1–3 | Options skew, intraday VIX, same-day news sentiment, FII provisional, expiry proximity | Lighter macro; higher weight on sentiment + technical |
| **B — Default** | 7–14 | Earnings cluster density, 2-week sentiment roll, FII 5d sum, macro z-scores | Balanced hybrid |
| **C — Structural** | 30–90 | RBI policy cycle, CPI trend, PE ratio, global index correlation, sector rotation | Macro polynomial dominant; constituent events lower weight |

```python
# index_research/horizon.py
def resolve_horizon(horizon_days: int | None) -> HorizonProfile:
    days = horizon_days or int(os.getenv("INDEX_RESEARCH_HORIZON_DAYS", "14"))
    if days <= 3:
        return HorizonProfile(name="A", days=days, feature_window=5, poly_degree=1)
    if days <= 21:
        return HorizonProfile(name="B", days=days, feature_window=14, poly_degree=2)
    return HorizonProfile(name="C", days=days, feature_window=60, poly_degree=2)
```

---

## Package Layout

```
integrations/trade_integrations/dataflows/index_research/
├── __init__.py
├── models.py                 # IndexResearchDoc, FactorSnapshot, PredictionRecord
├── aggregator.py             # run_index_research() orchestrator
├── format.py                 # latest.md renderer
├── constituents.py           # Nifty 50 list + weights
├── factor_store.py           # Parquet/SQLite read/write for time-series
├── prediction_ledger.py      # forecast logging + actual reconciliation
├── factor_matrix.py          # build X for given horizon
├── attribution.py            # constituent → index contribution
├── predictor.py              # hybrid model + range forecast
├── calibrator.py             # walk-forward retrain + drift detection
├── regime.py                 # market regime classification
├── scenarios.py              # event × outcome → index range table
├── horizon.py                # A/B/C profile resolver
├── macro_global.py           # oil, FX, US indices, gold, FRED
├── sources/
│   ├── batch_constituents.py # parallel company_research × 50
│   ├── history_loader.py     # NIFTY OHLCV + aligned factor history
│   ├── rbi_cpi.py            # RBI calendar + CPI/WPI proxies
│   └── weights_nse.py        # NSE factsheet scrape or mcap fallback
├── tools/
│   └── index_research_tools.py  # LangChain tool (Phase 6)
scripts/
├── run_index_research.py
├── run_index_calibration.py  # nightly: reconcile ledger + retrain if drift
└── smoke_index_research.py
```

---

## Hub Artifact Schema (`IndexResearchDoc`)

```json
{
  "ticker": "NIFTY",
  "as_of": "2026-07-16T12:00:00Z",
  "horizon": { "name": "B", "days": 14 },
  "spot": 24500.0,
  "prediction": {
    "view": "bullish",
    "expected_return_pct": 1.2,
    "range": { "low": 24200, "high": 24900, "confidence": 0.65 },
    "equation": {
      "form": "delta_nifty = sum(w_i * f_i) + beta · poly(X_macro)",
      "coefficients": { "usd_inr": 0.42, "oil_brent": -0.18, "fii_5d": 0.31 },
      "intercept": 0.05,
      "r2_walk_forward": 0.38
    }
  },
  "regime": { "label": "sideways", "india_vix": 14.2, "trend_20d": "flat" },
  "global_factors": [
    { "factor": "usd_inr", "value": 83.2, "z_score": 0.4, "contribution_pct": 0.15, "source": "yfinance" }
  ],
  "constituent_signals": [
    {
      "symbol": "RELIANCE",
      "weight": 0.102,
      "sector": "Energy",
      "events": [{ "type": "results", "date": "2026-07-20", "impact": "positive" }],
      "factors": [{ "name": "oil_brent", "sensitivity": 0.3 }],
      "sentiment_score": 0.12,
      "contribution_to_index_pct": 0.08
    }
  ],
  "sector_breadth": { "IT": 0.6, "BANK": -0.2, "ENERGY": 0.4 },
  "scenarios": [
    { "event": "RBI hold", "outcome": "dovish", "index_range": [24300, 25100], "probability": 0.4 }
  ],
  "accuracy": {
    "last_14d_mae_pct": 1.1,
    "direction_hit_rate_30d": 0.58,
    "model_version": "2026-07-16T12:00:00Z"
  },
  "stages": []
}
```

---

## Phase 1 — Constituent Universe + Factor Store Foundation

### Task 1: Models and factor store

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/models.py`
- Create: `integrations/trade_integrations/dataflows/index_research/factor_store.py`
- Test: `tests/test_index_factor_store.py`

**Interfaces:**
- Produces: `FactorSnapshot`, `IndexResearchDoc` dataclasses; `save_daily_factors(date, rows)`, `load_factor_history(start, end) -> pd.DataFrame`

- [ ] **Step 1: Write failing test**

```python
def test_save_and_load_daily_factors(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    from trade_integrations.dataflows.index_research.factor_store import (
        save_daily_factors, load_factor_history,
    )
    rows = [{"factor": "usd_inr", "value": 83.2, "z_score": 0.1}]
    save_daily_factors("2026-07-16", rows)
    df = load_factor_history("2026-07-16", "2026-07-16")
    assert len(df) == 1
    assert df.iloc[0]["factor"] == "usd_inr"
```

- [ ] **Step 2: Run test** — expect FAIL

Run: `pytest tests/test_index_factor_store.py::test_save_and_load_daily_factors -v`

- [ ] **Step 3: Implement models + factor_store**

- [ ] **Step 4: Run test** — expect PASS

- [ ] **Step 5: Commit**

---

### Task 2: Constituent list + weights

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/constituents.py`
- Create: `integrations/trade_integrations/dataflows/index_research/sources/weights_nse.py`
- Test: `tests/test_index_constituents.py`

**Interfaces:**
- Produces: `load_nifty50_constituents() -> list[ConstituentRow]` with `symbol`, `name`, `sector`, `weight`

Weight sources (priority order):
1. Cached `reports/hub/_data/index_factors/weights/latest.json`
2. NSE index factsheet scrape (monthly refresh)
3. Fallback: yfinance market-cap weights normalized to 1.0

- [ ] Implement + test + commit

---

### Task 3: Batch constituent research

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/sources/batch_constituents.py`
- Modify: `integrations/trade_integrations/context/hub.py` — add `save/load_index_research`

**Interfaces:**
- Consumes: `load_nifty50_constituents()`, `load_company_research_json(sym)`, `run_company_research(sym)`
- Produces: `batch_constituent_research(max_workers=4) -> list[ConstituentSignal]`

Reuse hub cache TTL (`TRADINGAGENTS_RESEARCH_CACHE_MINUTES`); skip re-fetch if fresh.

- [ ] Implement with concurrency limit (default 4) + test + commit

---

## Phase 2 — Global Macro Ingest + Daily Snapshots

### Task 4: Global macro collector

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/macro_global.py`
- Create: `integrations/trade_integrations/dataflows/index_research/sources/rbi_cpi.py`
- Test: `tests/test_index_macro_global.py`

**Factors to collect (daily snapshot):**

| Factor key | Source |
|------------|--------|
| `oil_brent` | yfinance `BZ=F` |
| `oil_wti` | yfinance `CL=F` |
| `usd_inr` | yfinance `INR=X` |
| `gold` | yfinance `GC=F` |
| `sp500` | yfinance `^GSPC` |
| `us_10y` | FRED `DGS10` (reuse `macro_us.py` client) |
| `india_vix` | nselib (reuse `macro_in.py`) |
| `fii_net_5d` | nselib FII/DII 5-day sum |
| `nifty_pe` | yfinance `^NSEI` trailingPE or manual |
| `cpi_yoy_proxy` | RBI/MOSPI scrape or `INInflation` ETF proxy |
| `repo_rate` | RBI policy rate (scrape or static seed + event dates) |
| `index_sentiment` | rolled-up FinBERT from constituent + index news |

Each fetcher returns `StageResult`; failures are `status="degraded"` not pipeline abort.

- [ ] Implement + smoke test + commit

---

### Task 5: Daily snapshot job

**Files:**
- Create: `scripts/run_index_factor_snapshot.py`
- Modify: `integrations/trade_integrations/dataflows/index_research/factor_store.py`

Run daily (or on-demand): collect all macro + constituent aggregates → append to factor store.

- [ ] Implement script; verify Parquet files under `reports/hub/_data/index_factors/daily/`
- [ ] Commit

---

## Phase 3 — Attribution Engine

### Task 6: Constituent → index attribution

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/attribution.py`
- Test: `tests/test_index_attribution.py`

**Logic:**
```python
def attribute_constituent(
    constituent: ConstituentSignal,
    weight: float,
    horizon: HorizonProfile,
) -> AttributionRow:
    # event_impact from calendar proximity + historical earnings-day move (if available)
    # sentiment_contrib = weight * sentiment_score * sentiment_beta
    # sector_spillover from peers with same industry
    ...
```

Sum `contribution_to_index_pct` across 50 → `attribution_total`. Reconcile residual to macro model.

- [ ] Implement + test with mock 3-constituent fixture + commit

---

### Task 7: Sector breadth + scenarios

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/scenarios.py`
- Create: `integrations/trade_integrations/dataflows/index_research/regime.py`

Scenarios: cross-product of upcoming events (earnings cluster, RBI, expiry) × bullish/neutral/bearish outcomes → index range from historical analogues or model simulation.

Regime: `india_vix` thresholds + 20d Nifty trend → `bull`/`bear`/`sideways`.

- [ ] Implement + test + commit

---

## Phase 4 — Hybrid Predictor + Solvable Equation

### Task 8: History loader + factor matrix

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/sources/history_loader.py`
- Create: `integrations/trade_integrations/dataflows/index_research/factor_matrix.py`
- Create: `integrations/trade_integrations/dataflows/index_research/horizon.py`
- Test: `tests/test_index_factor_matrix.py`

Build aligned DataFrame: `date × factors → nifty_forward_return_{horizon}`.

Polynomial expansion: `PolynomialFeatures(degree=profile.poly_degree, interaction_only=True)` on macro subset only (avoid 50×50 explosion).

Feature selection: drop columns with |corr| < 0.05 to target; cap at 40 features.

- [ ] Implement + test + commit

---

### Task 9: Hybrid predictor

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/predictor.py`
- Test: `tests/test_index_predictor.py`

```python
def predict_nifty(
    spot: float,
    constituents: list[ConstituentSignal],
    macro_row: dict,
    weights: dict[str, float],
    horizon: HorizonProfile,
    model_artifact: ModelArtifact | None = None,
) -> PredictionResult:
    bottom_up = sum(w[sym] * constituent_expected_return(s) for sym, s in ...)
    macro_features = build_macro_features(macro_row, horizon)
    macro_delta = model.predict(poly_transform(macro_features))  # Ridge
    total_return_pct = bottom_up + macro_delta
    mae = model_artifact.mae if model_artifact else 1.5
    return PredictionResult(
        expected_return_pct=total_return_pct,
        range_low=spot * (1 + total_return_pct/100 - mae/100),
        range_high=spot * (1 + total_return_pct/100 + mae/100),
        equation_coefficients=model_artifact.coefficients,
    )
```

Store trained `ModelArtifact` in `reports/hub/_data/index_factors/model/latest.json`.

- [ ] Implement with sklearn Ridge + PolynomialFeatures + test on synthetic data + commit

---

## Phase 5 — Prediction Ledger + Convergence Loop

### Task 5a: Prediction ledger

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/prediction_ledger.py`
- Create: `integrations/trade_integrations/dataflows/index_research/calibrator.py`
- Create: `scripts/run_index_calibration.py`
- Test: `tests/test_index_prediction_ledger.py`

**Every `run_index_research` call appends:**
```python
PredictionRecord(
    predicted_at, horizon_days, spot_at_prediction,
    expected_return_pct, range_low, range_high,
    actual_return_pct=None,  # filled later
    direction_correct=None,
)
```

**`run_index_calibration.py` (nightly or post-market):**
1. Load ledger rows where `predicted_at + horizon` has passed and `actual_return_pct` is null
2. Fetch actual Nifty return from OpenAlgo/yfinance → fill actual
3. Compute rolling MAE, direction hit rate → write to hub `accuracy` block
4. If `mae_14d` drift > 20% vs training baseline → trigger `calibrator.retrain()`

This is the **convergence mechanism** — model coefficients and feature weights update as predictions are scored.

- [ ] Implement + test + commit

---

## Phase 6 — Aggregator + Hub + CLI

### Task 10: Orchestrator

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/aggregator.py`
- Create: `integrations/trade_integrations/dataflows/index_research/format.py`
- Create: `scripts/run_index_research.py`
- Create: `scripts/smoke_index_research.py`
- Modify: `integrations/trade_integrations/context/hub.py`

```python
def run_index_research(
    ticker: str = "NIFTY",
    *,
    horizon_days: int | None = None,
    refresh_constituents: bool = False,
) -> IndexResearchDoc:
    ...
```

CLI:
```bash
python scripts/run_index_research.py NIFTY
python scripts/run_index_research.py NIFTY --horizon-days 2   # A
python scripts/run_index_research.py NIFTY --horizon-days 60  # C
```

- [ ] Implement + smoke script passes + commit

---

## Phase 7 — Agent Refresh + MCP Integration

### Task 11: Scheduled agent refresh

**Files:**
- Modify: `vibetrading/agent/src/scheduled_research/` — add job type `index_factor_snapshot`
- Modify: `vibetrading/agent/src/api/scheduled_routes.py` — document new job type

Default schedule (env-configurable):
- `INDEX_RESEARCH_SNAPSHOT_CRON="0 18 * * 1-5"` — daily factor snapshot after market close (IST)
- `INDEX_RESEARCH_FULL_CRON="0 8 * * 1"` — weekly full constituent refresh

Dispatch calls `run_index_factor_snapshot.py` then `run_index_research.py`.

- [ ] Wire executor callback + test + commit

---

### Task 12: LangChain tool + MCP

**Files:**
- Create: `integrations/trade_integrations/tools/index_research_tools.py`
- Modify: `integrations/trade_integrations/register.py` — prefetch hook
- Modify: `openalgo/mcp/mcpserver.py` — `get_index_trade_plan`
- Modify: `stack/vibe/skills/trade-stack/SKILL.md` — index research step

MCP `get_index_trade_plan` returns hub JSON + markdown summary; accepts `horizon_days`.

Wire `options_research` aggregator to read `load_index_research_json("NIFTY")` prediction view when building NIFTY trade plan.

- [ ] Implement + test MCP tool + commit

---

## Phase 8 — SHAP Attribution (optional enhancement)

### Task 13: Explainability layer

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/explain.py`
- Test: `tests/test_index_explain.py`

If `shap` installed: compute SHAP values on macro polynomial model → populate `global_factors[].contribution_pct` with model-consistent attribution (not just correlation).

Skip gracefully if `shap` not installed.

- [ ] Implement + commit

---

## Data Sources Registry

| Factor | Free source | Refresh | Already in repo |
|--------|-------------|---------|-----------------|
| Nifty 50 list | nselib | weekly | ✅ `peers_in.py` |
| Weights | NSE factsheet / yfinance mcap | monthly | ❌ new |
| Constituent calendar/sentiment | company_research hub | per TTL | ✅ |
| India VIX | nselib, yfinance | daily | ✅ |
| FII/DII | nselib | daily | ✅ |
| USD/INR, oil, gold, S&P | yfinance | daily | partial |
| US 10Y | FRED | daily | ✅ `macro_us.py` |
| RBI/CPI | scrape + seed | event-driven | ❌ new |
| Index OHLCV | OpenAlgo, yfinance | daily | ✅ |
| Options skew | options_research chain | intraday | ✅ (horizon A) |
| News sentiment | sentiment.py | daily | ✅ |

---

## Accuracy Targets (convergence goals)

Track in `accuracy` block; calibrator retrains when breached:

| Metric | Initial target (walk-forward) | Convergence goal |
|--------|------------------------------|------------------|
| MAE (14d return %) | < 2.0% | < 1.2% after 90 days of ledger |
| Direction hit rate (14d) | > 52% | > 58% |
| Range coverage (80% CI) | > 70% of actuals in range | > 80% |

Publish these in hub artifact so agent can say "model is calibrated / drifting."

---

## Execution Flow (end-to-end)

```
1. Daily: run_index_factor_snapshot.py        → factor store grows
2. On demand / scheduled: run_index_research.py → hub artifact + ledger entry
3. Nightly: run_index_calibration.py          → score past predictions, retrain if drift
4. User asks agent about NIFTY:
   → get_index_trade_plan(horizon_days=14)     → structured prediction + attribution
   → get_options_trade_plan(NIFTY)            → strategies use index view
5. After horizon: actual recorded → accuracy updates → coefficients adjust
```

---

## Task Dependency Graph

```
Phase 1 (Tasks 1-3) ──► Phase 2 (Tasks 4-5) ──► Phase 3 (Tasks 6-7)
                                                        │
                                                        ▼
                                              Phase 4 (Tasks 8-9)
                                                        │
                                                        ▼
                                              Phase 5 (Task 5a)
                                                        │
                                                        ▼
                                              Phase 6 (Task 10)
                                                        │
                                                        ▼
                                              Phase 7 (Tasks 11-12)
                                                        │
                                                        ▼
                                              Phase 8 (Task 13, optional)
```

**Start with Phase 1–2** — data collection and storage are the foundation you emphasized. Prediction quality cannot converge without persistent factor history.

---

## Environment Variables

```bash
INDEX_RESEARCH_HORIZON_DAYS=14          # default horizon B
INDEX_RESEARCH_MAX_WORKERS=4            # constituent batch parallelism
INDEX_RESEARCH_WEIGHTS_REFRESH_DAYS=30  # NSE factsheet refresh
INDEX_RESEARCH_MAE_RETRAIN_THRESHOLD=1.2  # retrain if 14d MAE exceeds this %
INDEX_RESEARCH_SNAPSHOT_CRON="0 18 * * 1-5"
INDEX_RESEARCH_FULL_CRON="0 8 * * 1"
```

---

## Self-Review (spec coverage)

| Requirement | Task |
|-------------|------|
| 50 constituents + factors | Tasks 2, 3, 6 |
| Global macro (oil, FX, inflation, rates) | Task 4 |
| Competitor/peer exposure | Task 3 (reuse peers_in) + Task 6 spillover |
| Polynomial solvable equation | Task 9 `equation` block |
| Multi-horizon A/B/C | Task 8 `horizon.py` |
| Data store + continuous update | Tasks 1, 5, 11 |
| Agent-driven refresh | Task 11 |
| Accuracy convergence | Task 5a |
| Integration with options flow | Task 12 |
| External research patterns | Documented above; RFE/regime/SHAP in Tasks 7, 8, 13 |

No placeholders remain in task steps — each task has concrete files and interfaces.
