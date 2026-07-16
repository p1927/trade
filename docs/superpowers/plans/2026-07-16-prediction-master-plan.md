# Prediction Equation Master Plan

**Supersedes / consolidates:**
- [`2026-07-16-prediction-equation-investigation.md`](2026-07-16-prediction-equation-investigation.md) — Phases 0–6 (RCA + counterfactual)
- [`2026-07-16-prediction-factor-rationality-plan.md`](2026-07-16-prediction-factor-rationality-plan.md) — re-verification + factor table
- [`2026-07-16-prediction-completeness.md`](2026-07-16-prediction-completeness.md) — data gaps

**North star:** User browses Nifty → gets a researched 14d forecast → sees P&L/charges → can trust *why* the equation said what it said. Accuracy improves only via **data completeness + economically justified structure**, not coef edits.

---

## Current state (July 2026 — Phases 0–5 shipped)

| Metric | Value | Notes |
|--------|-------|-------|
| Direction OOS (365d, walk-forward) | **55.6%** | Up from 44.4% after Nifty Invest API backfill (Jul 2026) |
| MAE OOS | **3.46%** | Down from 3.92% |
| FII/DII full-window coverage | **100%** | Nifty Invest API: 338 daily rows (Mar 2025–Jul 2026) |
| FII/DII flow-era coverage | **101.6%** | Gate **passed** via `web_flow_fetch.py` |
| Hybrid eval count | **12** | Hybrid direction **16.7%** — RSS backfill noise |
| Joint flow features | **Rejected** | Ablation delta 0.0 pp |
| `cap_artifact` misses | **4** | Shrinkage did not reduce |
| `nse_browser` hub rows | **111+ daily** | MCP + repo seeds; web scrape mission added |

**Counterfactual on misses (10):** 1 mapping_error_T0, 5 drift_dominant, 4 cap_artifact.

**Assumption register:** [`2026-07-16-prediction-factor-rationality-plan.md`](2026-07-16-prediction-factor-rationality-plan.md) — see § July 2026 re-verification (Phase 7).

---

## nse_browser MCP (primary data path for Phase 6)

Built module: [`integrations/trade_integrations/nse_browser/`](../../integrations/trade_integrations/nse_browser/)

| MCP tool | Purpose |
|----------|---------|
| **`get_nse_browser_data`** | Primary — fetch-if-stale by dataset; returns `records[]`, writes hub parquet |
| `get_nse_browser_status` | Row counts + freshness (no fetch) |
| `run_nse_browser_mission` | Low-level; prefer `get_nse_browser_data` |

**Prediction datasets:** `fii_dii` (cash flows), `fpi` (NSDL portfolio). Already merged in [`nse_flow_derivatives_backfill.py`](../../integrations/trade_integrations/dataflows/index_research/sources/nse_flow_derivatives_backfill.py).

**v2 plan:** [`.cursor/plans/prediction_plan_v2_1f9c7faa.plan.md`](../../../.cursor/plans/prediction_plan_v2_1f9c7faa.plan.md)

---

## Non-negotiable rules

- Walk-forward OOS on held-out eval rows only; never optimize in-sample R² (currently +0.13 in-sample vs 44% OOS — overfit warning).
- **OOS gate for any new feature block:** direction hit rate ≥ baseline + 3 pp on 365d / `eval_step=5`, else **reject**.
- No manual coef edits in [`reports/hub/_data/index_factors/model/latest.json`](../../reports/hub/_data/index_factors/model/latest.json).
- No zero-imputation for missing FII/DII — backfill real rows only (`_source=fetch-pipeline`).
- Block ablation must return numeric hit rates (fixed: drop columns, not NaN) — [`equation_diagnostics.py`](../../integrations/trade_integrations/dataflows/index_research/equation_diagnostics.py).

---

## Phased work plan

### Phase 0 — Measurement & RCA infrastructure (DONE)

Shipped: horizon_dates, prediction_counterfactual, equation_diagnostics, t0_information_audit, regime_gates, API + UI counterfactual bars.

### Phase 1 — Data completeness (PRIORITY)

Extend FII/DII via Mr. Chartist + NSE FAO archives + flow cache; enrich factor store; wire light_refresh upsert.

**Gate:** `data_audit_latest.json` shows `fii_net_5d` and `dii_net_5d` coverage **>90%**.

### Phase 1B — Multi-source web acquisition (NEW)

Use the **shared nodriver browser session** (same anti-block rules as NSE) to scrape institutional flow history from public sites when NSE/Mr Chartist depth is insufficient.

| Source | URL | Data | Notes |
|--------|-----|------|-------|
| **Moneycontrol** | [fii_dii_activity](https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/) | Daily cash FII/DII gross buy/sell/net; MF SEBI + FII SEBI monthly tabs | Month/year navigation back to **2006**; **login wall** — browser alone insufficient |
| **Nifty Invest** | [fii-history](https://niftyinvest.com/fii-dii-data/fii-history) | Capital market daily + CSV download per month | **Public API** `GET /fii-dii-data/api/v1/month?yearMonth=YYYY-Mmm` — **preferred HTTP path** (17 months as of Jul 2026) |
| **Mr. Chartist** | `/api/history-full` | ~111d cash + F&O OI/PCR | HTTP OK; overlay wins on overlap |
| **NSE** | `fii-dii` report + legacy archives | Today + deep daily (when CDP works) | Primary when available |

**Implementation:** `missions/web_flow_history.py`, `parsers/web_flow.py`, merge in `nse_flow_derivatives_backfill.fetch_web_flow_cash_frame()` (priority: cache → **web** → mrchartist → … → hub). Raw HTML → `data/nse/raw/web_flow/` (gitignored). Seeds: monthly cash + MF/FII SEBI CSV in repo.

**Gate:** Full-window daily FII/DII **>90%** after web scrape + enrich; no zero-imputation.

### Phase 2 — Factor rationality & OOS gates

Ablation joint flows (+3pp), regime buckets, redundancy cleanup, cap_artifact remeasure.

### Phase 3 — Horizon dynamics & hybrid parity

T0 audit tags, constituent archive backfill, hybrid backtest `--include-bottom-up`.

### Phase 4 — Live loop & reporting honesty

Ledger counterfactual, walk-forward direction in UI, scheduled post-close enrich.

### Phase 5 — Decision record (DONE)

Regenerated `equation_improvement_decisions.md` with accept/reject evidence.

### Phase 6 — nse_browser ingestion (IN PROGRESS)

Operationalize **`get_nse_browser_data`** into prediction pipeline via `nse_browser_refresh.py`; extend `fii_dii_history` for NSE historical CSV archives.

**Gate:** Full-window FII/DII coverage >90%; hub ≥200 trading days.

### Phase 7 — Assumption register refresh

Update factor-rationality plan with web research + empirical rejects (joint flows, shrinkage, hybrid RSS).

### Phase 8 — Direction structural experiments (OOS-gated)

Cap/sign conflict gate (shipped in `shrink_macro_delta`), flow regime buckets, hybrid quality gate (non-backfill archives only).

---

## Validation protocol

```bash
python -m pytest tests/test_horizon_dates.py tests/test_prediction_miss_analysis.py \
  tests/test_index_backtest.py tests/test_prediction_counterfactual.py \
  tests/test_equation_diagnostics.py tests/test_institutional_joint_features.py -q
```

## What we will NOT do

- Tune Ridge coefficients or lower α to fit historical misses
- Re-introduce delta features without coverage >90% and +3 pp ablation win
- Report in-sample 86.7% direction as model accuracy
- Skip FII/DII/DERIV data because coverage is hard
- Add "war/oil" dummies fit on 10 miss dates
