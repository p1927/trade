# Prediction Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all partial/missing Nifty prediction requirements: full historical FII/DII/derivatives data, retroactive company news archives, per-constituent factor visibility, mega-drop drill-down, polynomial equation display, and live refresh — end-to-end in UI.

**Architecture:** Extend factor store backfill (nselib + Mr. Chartist, drop seeded rows), add retroactive `company_research/history/{date}.json` via Google News RSS sweeps, enrich backtest `major_drawdowns` with constituent attribution, expose derivatives factor history API, and wire new frontend panels into row-wise Prediction page.

**Tech Stack:** Python 3.11+, nselib, pandas, FastAPI trade routes, React/TypeScript, existing factor_store parquet, hub JSON archives.

## Global Constraints

- Open-source / free APIs only (NSE public, Mr. Chartist, Google News RSS, yfinance, nselib).
- India execution via OpenAlgo; prediction data in `reports/hub/`.
- No new paid vendors. No test/demo/doc files unless required for verification.
- Match existing `integrations/trade_integrations/` and `vibetrading/frontend/` patterns.

---

## Phase 1 — Data backfill foundation

### Task 1: Extended FII/DII/derivatives flow backfill

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/sources/nse_flow_derivatives_backfill.py`
- Modify: `integrations/trade_integrations/dataflows/index_research/factor_backfill_enrichment.py`
- Modify: `integrations/trade_integrations/dataflows/index_research/factor_matrix.py`

**Interfaces:**
- Produces: `fetch_flow_derivatives_frame(start, end) -> DataFrame` columns: `date, fii_net, dii_net, nifty_pcr, fii_idx_fut_long, fii_idx_fut_short, _source`
- Produces: `merge_flow_history(days=365) -> dict` summary for scripts

- [ ] Implement Mr. Chartist `history-full` parser with `_source` filter (drop seeded rows)
- [ ] Add nselib `fii_dii_trading_activity` chunk merge for gaps
- [ ] Store `dii_net`, `dii_net_5d`, `fii_fut_long_short_ratio` in factor store via enrichment
- [ ] Add new keys to `MACRO_FACTOR_KEYS`

### Task 2: Derivatives factor history API

**Files:**
- Modify: `vibetrading/agent/src/api/trade_routes.py`
- Modify: `integrations/trade_integrations/dataflows/index_research/factor_store.py` (if needed)

- [ ] `GET /trade/index-prediction/derivatives-history?days=365` returns PCR, FII fut OI, DII net series
- [ ] Reuse factor store wide pivot; default factors: `nifty_pcr, fii_net_5d, dii_net_5d, fii_fut_long_short_ratio`

### Task 3: Retroactive company news archive

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/company_news_backfill.py`
- Create: `scripts/backfill_company_news_history.py`

- [ ] Google News RSS per symbol per trading day (`after:DAY before:DAY+1`)
- [ ] Save minimal `history/{YYYY-MM-DD}.json` with `news.headlines`, `sentiment.score`
- [ ] Batch Nifty 50 symbols, 180 trading days, rate-limited

---

## Phase 2 — Attribution & backtest depth

### Task 4: Drawdown constituent drill-down

**Files:**
- Create: `integrations/trade_integrations/dataflows/index_research/drawdown_attribution.py`
- Modify: `integrations/trade_integrations/dataflows/index_research/backtest_runner.py`

- [ ] For each `major_drawdowns` row: top 5 constituents by `weight * 1d_return`
- [ ] Attach archived news headlines per constituent for that date
- [ ] Extend `IndexBacktestDrawdown` type + `BacktestEvaluationPanel` expand UI

### Task 5: Per-constituent macro factor map in UI

**Files:**
- Modify: `vibetrading/frontend/src/components/prediction/ConstituentDetailPanel.tsx`

- [ ] Section "Factors that affect this stock" from `signal.factors` macro links
- [ ] Show archived headline timeline when history has research rows

---

## Phase 3 — Equation, levels, live refresh

### Task 6: Polynomial equation display

**Files:**
- Modify: `vibetrading/frontend/src/components/prediction/EquationCard.tsx`

- [ ] Render expanded polynomial: intercept + Σ(coef × term) with human labels
- [ ] Show implied Nifty level from bottom-up + macro blocks

### Task 7: Nifty levels everywhere

**Files:**
- Modify: `PredictionSummary.tsx`, `ForecastHistorySection.tsx`, `BacktestEvaluationPanel.tsx`

- [ ] Primary display = index level; % as secondary
- [ ] History chart Y-axis = implied Nifty level not only return %

### Task 8: Live refresh defaults

**Files:**
- Modify: `.env.example`, `vibetrading/agent/.env.example`
- Modify: `PredictionControls.tsx` (poll interval presets: 1m, 5m, 15m)

- [ ] Document `INDEX_MONITOR_ENABLE_SCHEDULER=1`
- [ ] Default poll 5 min visible in UI

---

## Verification

```bash
# Backfill
PYTHONPATH=integrations python scripts/backfill_company_news_history.py --days 180 --symbols NIFTY50
PYTHONPATH=integrations python -c "from trade_integrations.dataflows.index_research.factor_backfill_enrichment import enrich_factor_history; print(enrich_factor_history(days=365))"

# Backtest with drawdowns + constituents
PYTHONPATH=integrations python -c "from trade_integrations.dataflows.index_research.backtest_runner import run_and_save_backtest; print(run_and_save_backtest(days=365))"

# API probe
curl "http://127.0.0.1:8899/trade/index-prediction/derivatives-history?days=365"
curl "http://127.0.0.1:8899/trade/index-prediction/backtest?ticker=NIFTY&refresh=true&days=365"
```

**Success criteria:**
- Factor store has `dii_net_5d` + PCR for 200+ trading days
- At least 30 days of archived company news per top-10 constituents
- Drawdown rows show constituent movers + headlines
- Derivatives panel charts 12 months PCR/FII OI
- Equation card shows readable polynomial terms
- Playground + simulate return stable Nifty levels
