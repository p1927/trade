# Phase 1 — Data Ingest & Panel Integrity

**Goal:** What data the Ridge model may use; fail-closed gates before promotion.

**Concept:** Financial time-series models require aligned panels and walk-forward validation — not random k-fold ([DCF Modeling](https://www.dcfmodeling.com/blogs/blog/leveraging-regression-financial-model)). Missing data must be fixed at ingest, not zero-filled in the model ([prediction-data-completeness rule](.cursor/rules/prediction-data-completeness.mdc)).

---

## File 1: `prediction_data_requirements.py`

**Role:** Canonical lists — `REQUIRED_COLD_DATASETS`, `PANEL_DERIVED_FACTORS`, `PINNED_FOR_AUDIT` (10 factors: oil_brent, usd_inr, sp500, us_10y, india_vix, fii_net_5d, dii_net_5d, nifty_pcr, repo_rate, nifty_pe).

**Key functions:**
- `pinned_factors()` — audit set
- `audit_prediction_panel_coverage()` — per-factor coverage, `pinned_missing_or_sparse`

**Data in → out:** Materialized panel DataFrame → audit report with coverage % and violations.

**Correctness:** Pinned factors must have ≥45% coverage and non-zero std in 500d window before panel promote.

**Issue:** News keys in `required_factor_keys()` but not in `PINNED_FOR_AUDIT` — sparse news won't block save but affects Ridge when news ridge enabled (C3).

---

## File 2: `history_panel.py`

**Key functions:**

```18:61:integrations/trade_integrations/dataflows/index_research/history_panel.py
def _join_annual_macro_by_year(...):
    """Annual join; NaN-fill only, never overwrite daily columns."""
```

```127:151:integrations/trade_integrations/dataflows/index_research/history_panel.py
def materialize_panel(...):
    """Build + invariant check + save_panel (WAP promote)."""
```

**Pipeline:** Cold-tier parquets → outer merge on `date` → `enrich_prediction_panel` → derived features → invariants → save.

**Data out:** Wide daily panel: `date`, `close`, macro/flow/vix/news columns.

**Issues (N-10):** `load_aligned_panel_history` re-runs enrichment on load — values can drift from invariant-checked save. `_merge_on_date` silently drops duplicate column names from later frames.

---

## File 3: `panel_invariants.py`

**Gates:**
- `check_pinned_factor_gates` — coverage + variance
- `check_parent_derived_pairs` — parent has signal, derived flat
- `check_daily_protected_vs_cold` — panel vs cold-tier parity for protected dailies
- `check_panel_regression_vs_existing` — new save must not collapse vs previous

**Correctness:** `assert_panel_invariants` blocks `save_panel` unless `INDEX_PANEL_SAVE_FORCE=1`. Matches fail-closed north star.

---

## File 4: `history_ingest.py`

**Role:** Sync Nifty OHLCV tail, cold-tier backfills (NSE flows, RBI, macro daily). First stage in `run_index_research` (`aggregator` stage `history`).

---

## File 5: `macro_global.py` — `fetch_global_macro_snapshot`

**Role:** Live snapshot for pipeline run — oil, FX, SPX, yields, VIX, flows, PCR, calendar flags, index_sentiment from constituents.

**Data out:** `StageResult.factors` dict + `factor_rows` for factor store persistence.

**Issues (N-11, C8):**
- Velocities computed from factor-store history, not necessarily same as panel-derived columns used in training.
- `index_sentiment` omitted when no constituent sentiments on cached run — train/serve skew.
- Point-in-time yfinance vs EOD panel closes — intraday divergence possible.

---

## File 6: `data_completeness.py`

**Snippet — gate constant:**

```python
GATE_FAIL_MACRO_TRUST_MULTIPLIER = 0.5  # when FII/DII/PCR coverage fails
```

**Role:** `ensure_factor_data_complete` → `measure_flow_coverage` on factor store (365d). Aggregator sets `macro_trust_multiplier` to 0.5 when gate fails.

**Correctness (C8):** Gate is real and wired to predictor. PCR historic coverage ~73–79% documented; gate may fail on cached-only runs by design.

---

## File 7: `scripts/audit_prediction_data.py`

**Verified 2026-07-21:** Exit 0 on `--days 500`. Reports pinned coverage, news overlay readiness, debate backtest eligibility.

---

## Phase 1 verdict

**On the right path** for data integrity — panel invariants and audit script enforce completeness before model trust. Main gaps: **live vs panel derivation parity** (N-10, N-11) and **news factor pinning** (C3).

---

## Issues logged

| ID | Severity |
|----|----------|
| N-10 | medium |
| N-11 | medium |
| C3 | confirmed (news not pinned) |
| C8 | partial (gate works; UI surfacing) |
