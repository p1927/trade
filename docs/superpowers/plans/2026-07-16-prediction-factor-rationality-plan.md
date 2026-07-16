# Prediction Factor Rationality Plan

> **Goal:** Fix prediction equation issues discovered in the July 2026 investigation using **cause–effect reasoning and data completeness** — never blind coefficient tuning.
>
> **Baseline (pre-delta, walk-forward 365d):** 44.4% direction hit (18 eval rows), 4.50% MAE  
> **Regression (with delta features):** 35.3% direction hit (17 eval rows), 4.37% MAE  
> **Evidence:** Isolated ablation — delta features alone caused −9.1 pp direction; shrinkage had zero backtest effect without scenario anchor.

---

## Non-negotiable rules

| Allowed | Forbidden |
|---------|-----------|
| Walk-forward OOS on held-out eval rows | Retrain on full sample and claim improvement |
| Add features with economic + literature justification | Add features because they explain Feb–Apr 2026 misses |
| Joint FII–DII features (absorption ratio) per 2024–2026 literature | Separate contrarian FII without DII context |
| Regime gates pre-specified (VIX > 18, trend < −3%) | Tune regime thresholds on miss dates |
| Data backfill from NSE / Mr. Chartist (`_source=fetch-pipeline`) | Impute zeros for missing flow days |
| Block ablation must return numeric hit rates | Accept structural changes when ablation returns `null` |
| Revert features that fail OOS gate | Keep delta features because the story sounds right |

**OOS gate for any new feature block:** direction hit rate ≥ baseline + 3 pp on same 365d / eval_step=5 protocol, or feature rejected.

---

## Data sources (historical + real-time)

| Data | Official / primary source | Our pipeline | Real-time (market hours / post-close) |
|------|---------------------------|--------------|--------------------------------------|
| FII/DII cash net (₹ Cr) | [NSE FII/DII report](https://www.nseindia.com/reports/fii-dii) + historical archives | **`nse_browser`** MCP `get_nse_browser_data(dataset="fii_dii")` → hub parquet → `load_nse_browser_fii_dii_frame` | `get_nse_browser_data(refresh=true)` post-close |
| FII/DII history + F&O OI, PCR | [Mr. Chartist API](https://fii-diidata.mrchartist.com/data-api.html) — supplemental (~111 days) | `fetch_mrchartist_flow_frame`, `/api/history-full` | `GET /api/data` (latest session) |
| NSDL FPI debt/equity | [NSDL FPI reports](https://www.fpi.nsdl.co.in/) | **`nse_browser`** `get_nse_browser_data(dataset="fpi")` | Same |
| Nifty spot / technicals | yfinance `^NSEI` | `history_loader.load_nifty_history` | yfinance on light_refresh poll |
| India VIX | yfinance `^INDIAVIX` | `macro_global._fetch_india_vix` | Same |
| Global (oil, USD/INR, S&P, gold) | yfinance | `macro_global._YFINANCE_FACTORS` | Same |
| Nifty PCR (live) | OpenAlgo option chain | `macro_global._fetch_nifty_pcr` | OpenAlgo MCP when connected |
| RBI repo | RBI schedule | `rbi_repo_schedule.repo_rate_on` | Calendar lookup |
| Constituent research | Hub `company_research/history/{date}.json` | `company_news_backfill` | `batch_constituent_research` on refresh |

**Coverage gap root cause (updated July 2026):** `nse_browser` MCP is built but hub has only 1 day; Mr. Chartist caps at ~111 days; NSE `fiidiiTradeReact` is today-only. **Mitigation:** Phase 6 — `get_nse_browser_data(dataset="fii_dii", refresh=true)` + historical CSV discovery on NSE archives hub, then `enrich_factor_history`.

**Cross-link:** [`2026-07-16-prediction-master-plan.md`](2026-07-16-prediction-master-plan.md) Phase 6; v2 plan `.cursor/plans/prediction_plan_v2_1f9c7faa.plan.md`.

---

## July 2026 re-verification (challenge prior assumptions)

| Prior assumption | New evidence | Revised stance |
|------------------|--------------|----------------|
| `fii_net_5d` level → 14d direction | OOS corr −0.44; [JCAR 2024](https://doi.org/10.21863/jcar/2024.13.4.008): Nifty Granger-causes FII, not reverse | Flows are **regime context**, not standalone linear drivers at 14d |
| `institutional_net_5d` + DAR improve OOS | Ablation **delta 0.0 pp — rejected** | Need **regime-conditional buckets**, not linear joint features |
| Scenario shrinkage fixes cap artifacts | Still **4** cap misses after shrinkage | Fixes magnitude, not **sign conflict** when saturated bullish raw meets bearish outcome |
| Hybrid bottom-up improves direction | **16.7%** vs **44.4%** macro (n=12) | RSS lexicon backfill (`backfill: true`) adds noise — gate on archive quality |
| 44% OOS means broken equation | Ridge + static macro at 14d is structurally hard; small eval set (18 rows) | Target **calibrated confidence + range honesty**, not coef chasing |

**Still rejected:** delta features (−9.1 pp OOS), joint flow linear block, lower Ridge α, widen macro cap.

---

## Success metrics

| Metric | July 2026 actual | Target |
|--------|------------------|--------|
| Direction OOS (365d) | **44.4%** | ≥47% only if Phase 8 structural change passes +3 pp gate |
| FII/DII full-window coverage | 49.6% (flow-era 100%) | >90% full-window after Phase 6 nse_browser |
| Block ablation | numeric | joint_flows rejected (0.0 pp) |
| Cap_artifact misses | 4 | ≤2 after Phase 8 cap/sign gate |
| Hybrid eval rows | 12 | Quality-gated (non-backfill archives only) |

---

## Factor-by-factor: assumption → challenge → mitigation

### Flows block

| Factor | Assumption we had | Literature / web challenge | Mitigation |
|--------|-------------------|---------------------------|------------|
| `fii_net_5d` | FII inflows → Nifty up | Post-2023: FII→Nifty causality weakened; FII selling often absorbed ([2025 FPI paper](https://rspublication.com/ijrm/2026/e1/16.pdf)); 14d corr **−0.44** in our window → contrarian at this horizon | Keep as **one leg** of joint flow; disable standalone contrarian in `trend_down` via regime gate |
| `dii_net_5d` | DII cushion → bullish | DII pro-cyclical (SIP into rising markets); **bidirectional** Granger with Nifty post-COVID ([IMFI 2025](https://doi.org/10.21511/imfi.22(3).2025.14)); 50% coverage → coef sign flip | **Backfill to >90%** before interpreting coef; add `dii_absorption_ratio` |
| `fii_net_5d_change_5d` etc. | Acceleration fixes path | **Failed OOS:** −9.1 pp direction; sparse after `diff()` on 50% coverage | **REVERTED** — do not train until coverage >90% and ablation passes |
| **`institutional_net_5d`** (new) | Total institutional impulse matters | FII+DII net = combined pressure on index | Add: `fii_net_5d + dii_net_5d` |
| **`dii_absorption_ratio`** (new) | DII offsets FII | DAR >1 = full absorption ([MDPI 2026](https://www.mdpi.com/1911-8074/19/5/315)); captures 2024–2026 regime | Add: `dii_net_5d / max(abs(fii_net_5d), ε)` when FII selling |
| `fii_fut_long_short_ratio` | Positioning signal | Top drift factor in counterfactual; path changes over 14 sessions | Keep; monitor drift attribution |
| `nifty_pcr` | Put/call fear gauge | Derivatives from Mr. Chartist; aligns with NSE participant OI | Keep; backfill via `merge_flow_derivatives_frame` |

### Momentum / technical block

| Factor | Assumption | Challenge | Mitigation |
|--------|------------|-----------|------------|
| `nifty_return_14d`, RSI, MA20 distance | Mean-reversion | Negative corr with 14d forward in our window; literature: momentum works low VIX, mean-rev high VIX ([regime guides](https://equitiesindia.com/glossary/market-regime-detection)) | **Regime gate:** reduce momentum block weight when `india_vix > 18` |
| `constituent_momentum_7d` | Breadth leads index | r=0.99 redundant with `nifty_return_7d` | Drop one from selection (prefer constituent for bottom-up parity) |

### Global block

| Factor | Assumption | Challenge | Mitigation |
|--------|------------|-----------|------------|
| `sp500` | Risk-on spillover | **#1 drift** factor T0→T1 in counterfactual | Keep; document as path risk not level-only |
| `oil_brent` | Import cost → Nifty down | Positive coef in our model vs negative literature | Use level + monitor; India oil pass-through is lagged and fiscal |
| `usd_inr` | Rupee weakness → outflows | Collinear with global risk | Keep one of usd_inr / sp500 via redundancy register |

### Volatility block

| Factor | Assumption | Challenge | Mitigation |
|--------|------------|-----------|------------|
| `india_vix` | High VIX → down | Positive coef in stored model — sign conflict | VIX as **regime switch**, not only linear return driver |
| `nifty_realized_vol_20d` | Vol clusters | Correlated with VIX (r>0.82) | Keep one primary vol feature in ablation |

### Calendar block

| Factor | Assumption | Challenge | Mitigation |
|--------|------------|-----------|------------|
| `is_results_season`, `is_budget_week` | Event seasonality | Drift contributor on misses | Keep dummies; enrich event_gap with T0 headline diff (done) |

### Sentiment / redundant

| Factor | Assumption | Challenge | Mitigation |
|--------|------------|-----------|------------|
| `index_sentiment` vs `sector_breadth_mean_sentiment` | Two signals | r=1.0 redundant | Train on `index_sentiment` only; drop duplicate from matrix |

---

## Implementation phases

### Phase A — Revert proven harm (immediate)
- Remove delta features from `MACRO_FACTOR_KEYS` and Ridge training
- Mark `delta_features` **rejected** in decision record with OOS evidence
- Target: restore ~44% direction baseline

### Phase B — Data completeness (prerequisite for flow interpretation)
- Purge `None.csv` / anomalous factor daily files
- Run `enrich_factor_history` over 365d trading window
- Merge priority: Mr. Chartist `history-full` (real rows) + NSE `fiidiiTradeReact` for today
- Live path: Mr. Chartist `/api/data` fallback when nselib unavailable
- **Gate:** FII/DII coverage >90% in `data_audit_latest.json`

### Phase C — Joint flow features (literature-aligned)
- Add `institutional_net_5d`, `dii_absorption_ratio` to history + factor store
- **Gate:** walk-forward ablation vs baseline; keep only if ≥+3 pp OOS

### Phase D — Regime gates (pre-specified, wired)
- Apply `regime_gates` in `predict_nifty` and backtest
- Report OOS hit rate by regime bucket (`high_fear`, `trend_down`, `range_bound`)

### Phase E — Shrinkage with scenario anchor in backtest
- Pass `scenario_anchor_return_pct` from `build_index_scenarios` in walk-forward eval
- Measure cap_artifact miss count before/after

### Phase F — Fix validation infrastructure
- Block ablation: exclude factor columns from training matrix (not NaN)
- Reject any future structural change when ablation returns null

### Phase G — Hybrid parity
- Backfill `company_research/history` for eval miss dates only
- Run backtest with `--include-bottom-up`; report macro vs hybrid hit rates

---

## References

- NSE FII/DII: https://www.nseindia.com/reports/fii-dii
- Mr. Chartist API: https://fii-diidata.mrchartist.com/data-api.html
- FPI outflows 2025 / weakened causality: https://rspublication.com/ijrm/2026/e1/16.pdf
- DII dominance / DAR: https://www.mdpi.com/1911-8074/19/5/315
- DII–Nifty Granger bidirectional: https://doi.org/10.21511/imfi.22(3).2025.14
- JCAR 2024 FII/DII Granger (returns → flows): https://doi.org/10.21863/jcar/2024.13.4.008
- Short-horizon macro staleness: https://doi.org/10.54254/2754-1169/2025.bj30479
- nse_browser MCP: `get_nse_browser_data`, `get_nse_browser_status` in `openalgo/mcp/mcpserver.py`
- OOS predictability limits: Welch & Goyal (2008); emerging markets OOS study
- Regime / VIX switching: https://equitiesindia.com/glossary/market-regime-detection
