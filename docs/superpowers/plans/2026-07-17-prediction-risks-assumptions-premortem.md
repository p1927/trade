# Prediction System — Risks, Assumptions & Pre-Mortem

> Companion to [prediction algorithms master plan](2026-07-17-prediction-algorithms-master-plan.md), [prediction master plan](2026-07-16-prediction-master-plan.md), and [factor rationality register](2026-07-16-prediction-factor-rationality-plan.md).

**Purpose:** Document what remains after lab + Phase I ship, structural assumptions that cap accuracy, likely post-implementation surprises (“black swans”), and **mitigation policies** to implement in code later.

**Last updated:** July 2026 (post scoreboard UI + Phase I derive modules)

---

## 1. What is still left after full planned implementation

“Done” means plumbing + standard factors + scoreboard — **not** guaranteed direction skill above ~50% OOS or auto-promoted combiners.

| Area | Status after lab A–E + H1 + Phase I (partial) | Why it still matters |
|------|-----------------------------------------------|----------------------|
| **Phase G** | Not shipped | Split `quant_ridge` vs `event_overlay`; debate archive; LightGBM experiments — overlay double-count in quant biases combiner scores |
| **H2** | Deferred | SVAR / local projections — cause→channel lags stay heuristic |
| **H3** | Deferred | DoWhy DAG — causal claims stay narrative |
| **UI scoreboard** | **Shipped** | Prediction → **Track Scoreboard** tab: per-track Nifty replay (single + compare), return % chart, metrics tables, insufficient-evidence banner |
| **Phase F auto-promotion** | Partial | `combine` + `auto` wired; 2-run stability + bootstrap CI not shipped |
| **Hybrid backtest** | `hybrid_eval_count = 0` | Walk-forward macro-only ≠ live quant (bottom-up missing in history) |
| **Quality phases 5–7** | Partial | Bottom-up calibration, hybrid eval, direction calibration v2 |
| **News → Ridge history** | Partial | Cause layer live; Ridge may lack aligned historical `news_*_7d` |
| **Phase I data depth** | Partial | Derive modules shipped; real D/P, P/B history, CRISIL credit fail ablation if coverage &lt; 45% |
| **Autonomous integration** | Not wired | `forecast_tracks` not yet consumed by Nautilus / `/autonomous` revise loop |
| **Production promotion** | Report-only default | `eval_count ≥ 60` gate shipped; typical dev runs still have low n; headline stays `quant_only` |

---

## 2. Core assumptions (limit predictability)

| ID | Assumption | Limitation | Evidence (July 2026) |
|----|------------|------------|----------------------|
| A1 | 14d Nifty direction is predictable from slow macro + valuation | Short-horizon noise dominates | Direction OOS ~**50%**, MAE ~**3.5%** |
| A2 | Linear Ridge (+ poly) is adequate | Thresholds, asymmetry (VIX spike) missed | Many ablation blocks **rejected** (+3 pp gate) |
| A3 | Factor–return relationships are stationary | Regime breaks (COVID, rates, elections) | Walk-forward helps; **~18 eval rows** |
| A4 | Free/open data ≈ institutional quality | Delays, revisions, sparse fundamentals | FII improved; credit/dividend weak |
| A5 | More literature factors → better headline | Multicollinearity, combination puzzle | Sector/event flags rejected OOS |
| A6 | News tags ≈ economic causes | Mis-tags, verification lag | `cause_stress_index` is heuristic |
| A7 | Bottom-up constituent signal adds edge | Sentiment/momentum noisy | Hybrid backtest unvalidated |
| A8 | Single 14d headline serves user intent | 3d vs 21d strategies differ | One horizon artifact |
| A9 | Direction hit rate is the right success metric | Options P&amp;L depends on vol + path | Magnitude errors large |
| A10 | Daily walk-forward bars capture live timing | Open/close gaps, intraday moves invisible | Spot vs close mismatch possible |
| A11 | Combiner weights stable across windows | Timmermann puzzle — optimal weights unstable | Promotion requires 2-run stability |
| A12 | India behaves like generic EM literature | DII, retail, EPFO, SGB flows are unique | FII-only narrative incomplete |

---

## 3. Post-implementation surprises (likely discoveries)

### 3.1 Statistical mirage

- **Small n:** ~18 OOS eval rows → +3 pp promotion is often noise.
- **Track correlation:** `quant_ridge`, `macro_only`, `scenario_anchor` move together — combiners look good in-sample.
- **Combination puzzle:** Equal-weight / inverse-MAE may never beat `quant_only` on direction.

**Mitigation (code):** Bootstrap CI on direction delta; require `eval_count ≥ 60` for auto-promote; UI “insufficient evidence” state.

### 3.2 Backtest ≠ live parity

- Backtest **macro_only** lacks historical bottom-up; live **quant_ridge** includes it.
- **debate_numeric** live-only — not backtestable.
- News shock calibration uses **reconciled** stories; live headlines arrive earlier → lookahead or lag bias.

**Mitigation (code):** `backtest_eligible: bool` per track in scoreboard; never auto-promote non-eligible tracks.

### 3.3 Regime inversion

- FII flows sometimes contrarian (DII absorption, policy floors).
- VIX velocity spike → short-term panic then 14d mean-reversion rally.
- High ERP can persist (value trap) before mean reversion.

**Mitigation (code):** Regime labels on scoreboard slices; shrink macro trust in `high_fear`; document in assumption register per factor.

### 3.4 Data pipeline failures

| Risk | Impact |
|------|--------|
| NSE/RBI HTML/API change | FII, T-Bill, G-Sec stale silently |
| yfinance index metadata gaps | P/E, dividend yield wrong/NaN |
| No free India credit spread | `india_credit_spread` weak proxy |
| Scrape caps (~111d history) | Velocity/z-score features underpowered |
| Stack down during refresh | Stale hub artifact with fresh-looking timestamp |

**Mitigation (code):** Per-factor coverage % on artifact; flow gate pattern extended to Phase I keys; stale banner when `cause_stress ≥ 60` + age &gt; threshold.

### 3.5 Unmodeled event classes

- SEBI / budget surprise (STT, LTCG, FPI limits)
- Single-name index shock (suspension, governance)
- Election / border / sanctions before news tags
- Expiry-week gamma, block deals, liquidity air pockets

**Mitigation (code):** Scenario anchor + material news invalidation; `unmodeled_event_suspected` when stress high but factors flat.

### 3.6 LLM / narrative layer

- Agent debate contradicts quant on non-quantifiable grounds.
- Non-stationary; no OOS gate on debate quality over time.

**Mitigation (code):** Exclude `debate_numeric` from auto-promotion (plan rule); show as advisory column only.

### 3.7 Execution feedback (future)

- Autonomous orders move PCR, vol, short-term returns.
- Refresh loops create operational coupling.

**Mitigation (code):** Paper-first; size limits; do not feed execution P&amp;L into Ridge without lag (north star rule).

---

## 4. Black swans (low probability, high impact)

| ID | Event | Why model fails | Early warning (partial) |
|----|-------|-----------------|-------------------------|
| B1 | Capital controls / FPI restriction | No precursor in daily factors | Policy news; extreme USD/INR |
| B2 | Global correlation → 1 | All channels fire; double-count | `cause_stress_index` + multi-channel attribution |
| B3 | Index rebalance / weight cap | Bottom-up + historical coefs wrong | Calendar + corporate actions |
| B4 | Bad single data print | One stale FII/VIX poisons snapshot | Cross-vendor validation (future) |
| B5 | Lucky streak → false promotion | 2 wins on tiny n | Stricter promotion + CI |
| B6 | Retail narrative cascade | Not in verified news / FinBERT timely | Material news watcher (partial) |
| B7 | Broker/stack outage on expiry | Stale forecast shown as live | Health checks + stale UI |

---

## 5. Mitigation policy register (implement in code — phased)

| ID | Policy | Target module | Priority |
|----|--------|---------------|----------|
| M1 | `eval_count ≥ 60` + bootstrap CI for auto-promote | `promotion.py` | P1 — **partial** (`eval_count≥60` + UI banner shipped; bootstrap CI pending) |
| M2 | UI/API honest skill: direction %, n, “informational not tactical” | API + Prediction UI | P1 — **partial** (Track Scoreboard tab shows n, MAE, direction; no bootstrap CI) |
| M3 | Phase I factor coverage gate (≥45% rows, ≥180d) before Ridge | `phase_i_coverage.py`, `factor_matrix.py` | P1 — **shipped** |
| M4 | `backtest_eligible` flag per track in scoreboard | `registry.py`, `evaluator/walk_forward.py` | P1 — **shipped** |
| M5 | Stale + high stress banner blocks high confidence | aggregator, UI | P2 |
| M6 | Extend assumption register row per Phase I factor | `equation_diagnostics.py` / docs | P2 |
| M7 | `unmodeled_event_suspected` heuristic | `cause_stress_index.py` | P2 |
| M8 | Phase G split overlay for fair track lab | `predictor.py`, tracks | P2 |
| M9 | Hybrid backtest bottom-up history | `backtest_runner.py` | P3 |
| M10 | Cross-vendor spot/VIX sanity check | `data_completeness.py` | P3 |

---

## 6. Realistic outcomes after full implementation

| Outcome | Likely? |
|---------|---------|
| Richer explanation (valuation, spreads, ERP, track disagreement) | **Yes** |
| Improved factor catalog + coverage transparency | **Yes** |
| Direction OOS 58–60% from Phase I factors alone | **Unlikely** without long clean history |
| Auto-promoted combiner beats quant | **Possible**, gated; report-only valid |
| Catch all war/oil/Fed shocks within 14d | **No** |

---

## 7. Implementation order (agreed)

1. ~~**Phase I derive modules**~~ — ingest, derive, coverage gate (**partial** — real fundamental backfill pending).
2. ~~**Scoreboard UI**~~ + evidence labels (n, `backtest_eligible`, insufficient-evidence banner) — **shipped**.
3. **Phase G** — split overlay track; fair combiner scoring.
4. **Hybrid backtest** — bottom-up in walk-forward.
5. **Mitigations M1–M7** remaining (bootstrap CI, stale banner, unmodeled event).
6. **`INDEX_PREDICTION_COMBINER=auto`** only after M1 bootstrap + sufficient n + 2-run stability.

---

## Related documents

| Document | Role |
|----------|------|
| [2026-07-17-prediction-algorithms-master-plan.md](2026-07-17-prediction-algorithms-master-plan.md) | Lab + Phase I scope |
| [2026-07-16-prediction-master-plan.md](2026-07-16-prediction-master-plan.md) | Ridge OOS gates |
| [2026-07-16-prediction-factor-rationality-plan.md](2026-07-16-prediction-factor-rationality-plan.md) | Per-factor assumption register |
| [2026-07-16-prediction-quality-phased-fixes.md](2026-07-16-prediction-quality-phased-fixes.md) | Pipeline quality phases |
