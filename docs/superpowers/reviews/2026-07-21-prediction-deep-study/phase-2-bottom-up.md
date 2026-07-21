# Phase 2 — Constituent Bottom-Up Layer

**Goal:** How 50 NIFTY stocks become `bottom_up_return_pct`.

**Concept:** Hybrid bottom-up + top-down is standard in forecasting literature, but optimal combination uses hierarchical reconciliation ([Hyndman et al.](https://robjhyndman.com/papers/Hierarchical6.pdf), [index reconciliation](https://doi.org/10.1080/14697688.2024.2412687)). We use **simple addition** without reconciliation — documented limitation C1.

---

## Pipeline position (aggregator)

```200:276:integrations/trade_integrations/dataflows/index_research/aggregator.py
    constituent_mode = "full_refresh" if refresh_constituents else "cached_snapshot"
    if refresh_constituents:
        signals = batch_constituent_research(refresh=True, ...)
    else:
        cached_doc = load_index_research_json(sym)
        if cached_doc is None:
            raise RuntimeError("No index research snapshot ... run full analysis with 'Refresh all 50 constituents'")
        signals = signals_from_cached_doc(cached_doc)
```

**C6 confirmed:** Without prior full run + unchecked refresh, cached path fails or uses stale signals.

---

## File 1: `batch_constituents.py`

**Key functions:**
- `_research_one(symbol)` — cache hit → hub JSON; miss → `run_company_research`
- `batch_constituent_research(max_workers, lookahead_days, refresh=False)`

**Data in → out:** 50 × `ConstituentSignal` (weight, sentiment, factors[], momentum placeholder).

**Default `refresh=False`:** Fast path uses hub company_research cache. News ingest only when `refresh=True`.

---

## File 2: `constituent_momentum.py`

**Key functions:**
- `batch_fetch_returns_7d` — single yfinance batch download
- `attach_constituent_momentum` — sets `momentum_7d_pct` per signal
- `rollup_constituent_momentum` — weight-averaged 7d return

**Issues:**
- **N-08:** Dead code at L158–160 (unreachable partial-coverage branch)
- 7d uses ~10–12 calendar days, not trading days — misaligned with 14d horizon
- `constituent_momentum_7d` in macro keys but **excluded from Ridge training** — live bottom-up only (C1)

---

## File 3: `attribution.py`

**Core formula per stock:**

```65:84:integrations/trade_integrations/dataflows/index_research/attribution.py
def _expected_return_pct(signal, horizon_days=14):
    # sentiment * SENTIMENT_BETA (5.0) + momentum * MOMENTUM_SCALE (0.5)
    # earnings bump +0.5% if event in horizon
    # cap per-stock expected at ±3%
```

**Rollup:**

```168:187:integrations/trade_integrations/dataflows/index_research/attribution.py
def rollup_attribution(attributed):
    total = sum(s.contribution_to_index_pct for s in attributed)
    # contribution_to_index_pct = weight * expected_return_pct
```

**Data out:** `bottom_up_return_pct = rollup["total_contribution_pct"]` → fed to `predict_nifty`.

**Issues:**
- **N-09:** `_today()` for earnings window, not pipeline `as_of_day`
- Hardcoded betas; optional calibration swallows errors silently
- Missing momentum → sentiment-only names not down-weighted

---

## File 4: `predictor.py` consumption

```624:635:integrations/trade_integrations/dataflows/index_research/predictor.py
    attributed = attribute_constituents(signals, horizon_days=horizon.days, ...)
    rollup = rollup_attribution(attributed)
    bottom_up = float(rollup["total_contribution_pct"])
```

```684:684:integrations/trade_integrations/dataflows/index_research/predictor.py
    expected_return_pct = bottom_up + macro_delta  # before reconcile/debate
```

---

## Phase 2 verdict

Bottom-up is **transparent and inspectable** (per-stock drivers in UI). Not walk-forward validated in backtest (`hybrid_eval_count=0` in master plan). Coefficients are heuristic, not OOS-tuned — acceptable for v1 if labeled honestly.

---

## Issues logged

| ID | Severity |
|----|----------|
| C1 | confirmed |
| C6 | confirmed |
| N-08 | low |
| N-09 | medium |
