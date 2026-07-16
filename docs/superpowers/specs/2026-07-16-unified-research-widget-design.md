# Unified Research → Widget Pipeline — Design Spec

**Date:** 2026-07-16  
**Status:** Approved (hybrid C) — 2026-07-16  
**Goal:** Every trade-plan widget (options, stock, index) is populated only after required research completes. Prediction, targets, P&L, and charges reflect **TradingAgents debate + quantitative models**, merged with explicit provenance — not hardcoded ranker placeholders.

---

## Summary

Today the three asset pipelines share a hub layout but **no unified contract**:

- **TradingAgents debate** writes `agent_debate/latest.json` but nothing reads it when building trade plans or widgets.
- **Stock** uses sentiment heuristics and fixed ±5%/−3% target/stop; payoff/charge fields are often empty or mis-displayed.
- **Options** is the most complete path but debate still does not feed rankers.
- **Index** has a real predictor; stock/options do not inherit that pattern.
- **Charges** for F&O use `broker_charges/presets.json` (default INDmoney); equity uses inline Zerodha-style math disconnected from OpenAlgo session broker.

This spec introduces:

1. **`research/registry.py`** — single map of asset kinds → required stages, producers, hub paths, widget fields.
2. **`research/orchestrator.py`** — `ensure_research_complete()` runs missing stages before any widget emit.
3. **`research/debate_synthesis.py`** — merges debate structured output into trade-plan docs.
4. **`research/stock_predictor.py`** — lightweight quantitative band model for single-name equities (mirrors index predictor pattern at smaller scope).
5. **Unified broker charges** — equity CNC/MIS in `broker_charges/presets.json`; all pipelines resolve broker from OpenAlgo session → env → INDmoney default.
6. **Stricter presentability gates** — widget blocked until research-derived fields exist.

---

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Prediction authority | **Hybrid C** — debate for direction/catalysts/confidence; quantitative model for range bands; widget shows merged view with provenance |
| Research gate | **No widget without `ensure_research_complete`** — MCP tools and auto-emit call orchestrator first |
| Debate requirement | **Required** for stock and options finalize/execute intents; optional for index outlook-only if index_research fresh |
| Charges broker | **OpenAlgo session `broker` → env → `indmoney`** — same resolver for options, stock, Strategy Builder API |
| Hub as source of truth | Widget builders read **saved hub JSON** after orchestrator saves; no orphan aggregator runs |
| Debate → plan wiring | **New synthesis layer** writes merged fields into `*_research/latest.json` before widget build |
| UI | Stock/index show prediction block; charges to 2 dp; `null` displays as "—" not ₹0 |

---

## Research contract registry

**Location:** `integrations/trade_integrations/research/registry.py`

Each `ResearchKind` entry:

```python
@dataclass(frozen=True)
class ResearchStage:
    id: str                          # e.g. "company_research"
    producer: Literal["batch", "debate", "synthesis", "live_quote"]
    required: bool
    hub_subdir: str | None           # e.g. "company_research"
    freshness_env: str | None        # cache TTL env var
    parallel_group: int              # same group may run concurrently

@dataclass(frozen=True)
class ResearchKindContract:
    kind: Literal["options", "stock", "index"]
    eligibility: Callable[[str], bool]
    stages: tuple[ResearchStage, ...]
    hub_subdir: str                    # primary artifact dir
    save_fn: str                       # hub.save_* name
    load_fn: str
    aggregator: str                    # run_*_research
    widget_builder: str
    widget_intent: str                 # presentation_mode default
    required_widget_fields: tuple[str, ...]
```

### Options (`options`)

| Stage | Producer | Required | Notes |
|-------|----------|----------|-------|
| `company_research` | batch | yes (F&O underlyings) | Identity, events, sentiment |
| `options_research` | batch | yes | chain → events → analytics → rank → payoff |
| `agent_debate` | debate | yes on `execute_refresh` / finalize | Bull/bear/risk + judge |
| `debate_synthesis` | synthesis | yes when debate present | Merge into options doc |
| `live_quote` | live_quote | yes | Spot for charges/payoff |

**Hub:** `reports/hub/{SYMBOL}/options_research/latest.json`  
**Widget fields:** legs, payoff samples, max P/L (gross + net), charges (per-leg + round-trip), prediction (view + IV regime if available), scenarios, ranked strategies.

### Stock (`stock`)

| Stage | Producer | Required | Notes |
|-------|----------|----------|-------|
| `company_research` | batch | yes | Fundamentals, calendar, news, sentiment |
| `agent_debate` | debate | yes | Direction, catalysts, confidence |
| `stock_quant_predict` | batch | yes | 1d + horizon band from price history + volatility |
| `debate_synthesis` | synthesis | yes | Merge debate + quant → prediction block |
| `stock_research` | batch | yes | Rank strategies using merged prediction; build legs |
| `live_quote` | live_quote | yes | Entry price |

**Hub:** `reports/hub/{SYMBOL}/stock_research/latest.json`  
**Widget fields:** prediction (view, 1d range, horizon range, provenance), recommended (entry, target, stop, max P/L), charges (INDmoney round-trip), scenarios, ranked strategies, legs.

### Index (`index`)

| Stage | Producer | Required | Notes |
|-------|----------|----------|-------|
| `index_research` | batch | yes | Constituents, macro, predictor, scenarios |
| `agent_debate` | debate | optional | Enriches outlook narrative |
| `debate_synthesis` | synthesis | when debate present | Reconcile debate direction with predictor |
| `live_quote` | live_quote | yes | Index spot |

**Hub:** `reports/hub/{SYMBOL}/index_research/latest.json`  
**Widget fields:** prediction range, factor contributors, scenarios (existing index widget shape).

---

## Hybrid prediction merge (approach C)

### Inputs

1. **Quantitative model** (`stock_predictor.py` / existing `index_research/predictor.py`):
   - Historical OHLCV (yfinance / OpenAlgo)
   - Realized volatility, momentum, mean-reversion band
   - Outputs: `expected_return_pct`, `range.low`, `range.high`, `horizon_days`, `model_confidence`

2. **Debate synthesis** (`debate_synthesis.py`):
   - Parses `agent_debate/latest.json`: `rating`, `final_trade_decision`, judge decisions, analyst reports
   - LLM-assisted **structured extract** (deterministic JSON schema, no free-form-only):
     ```json
     {
       "view": "bullish|bearish|neutral",
       "direction_confidence": 0.72,
       "catalysts": ["earnings beat", "sector rotation"],
       "horizon_days": 1,
       "expected_return_pct": 1.2,
       "range": {"low": 1280, "high": 1320},
       "recommended_action": "buy_dip",
       "target": 1360,
       "stop": 1255,
       "rationale": "..."
     }
     ```
   - Fallback regex/heuristics when structured fields missing from debate prose.

### Merge rules

| Field | Priority | Rule |
|-------|----------|------|
| `view` (direction) | Debate | Quant model adjusts only if debate confidence < 0.4 |
| `expected_return_pct` | Weighted blend | `0.6 * debate + 0.4 * quant` when both present; else whichever exists |
| `range.low/high` | Quant primary | Debate narrows/widens band ±15% if strong catalyst disagreement |
| `horizon_days` | Intent-driven | User/autonomous "tomorrow" → `1`; default `14` |
| `target` / `stop` | Debate if numeric | Else derived from merged range (upper band / lower band) |
| `confidence` | `min(debate_conf, model_conf)` | Conservative combined score |
| `provenance` | Always | `{ "direction": "debate", "range": "quant", "targets": "debate|derived" }` |

Index path reuses existing `reconcile_prediction_with_scenarios()` after debate merge.

---

## Orchestrator flow

**Location:** `integrations/trade_integrations/research/orchestrator.py`

```python
def ensure_research_complete(
    ticker: str,
    *,
    kind: ResearchKind,
    refresh: bool = False,
    horizon_days: int = 14,
    require_debate: bool | None = None,  # None → from registry + intent
) -> ResearchResult:
    """
    Returns ResearchResult(status, doc, stages_run, missing, debate_pending).
    Saves hub artifact on success.
    """
```

### Algorithm

1. Load registry contract for `kind`.
2. For each stage (respecting `parallel_group`):
   - Skip if cache fresh and not `refresh`.
   - Run stage handler (dispatch to existing `run_*` functions).
   - Record `StageResult` on doc.
3. After batch + debate: run `debate_synthesis.merge_into_doc(doc, debate, quant)`.
4. Re-run rank/payoff/charges on merged doc (options: `estimate_strategy_metrics`; stock: rank + `build_stock_payoff` + charges).
5. Validate `required_widget_fields` — set `plan_status: "ready" | "incomplete"`.
6. `save_*_research(doc)`.
7. Return result; widget builders **only** accept `status == complete`.

### Debate async handling

- If debate not fresh and `require_debate`: start background debate (existing MCP pattern), return `debate_pending=True`, `plan_status: "partial"`.
- Widget emit blocked until synthesis completes (aligns with widget-intent-gating spec).
- Autonomous agent prompt: "If debate pending, tell user research in progress; do not emit execute widget."

### MCP integration

Wrap existing tools:

| Tool | Change |
|------|--------|
| `get_stock_trade_widget` | Call `ensure_research_complete(stock)` → `build_stock_trade_widget_from_doc(load)` |
| `get_options_trade_widget` | Same pattern |
| `get_index_trade_widget` | Same pattern |
| `get_*_trade_plan` | Same orchestrator before format |
| New: `get_research_status(ticker, kind)` | Expose stage checklist for agent/UI |

---

## Debate synthesis layer

**Location:** `integrations/trade_integrations/research/debate_synthesis.py`

### Functions

- `extract_structured_debate(debate_json) -> DebateForecast`
- `merge_stock_prediction(debate, quant, company_doc) -> dict`
- `merge_options_context(debate, options_doc) -> dict` — bias ranker scores, not replace chain analytics
- `merge_index_prediction(debate, index_doc) -> dict` — call existing reconcile helper

### Persistence

Merged result written into primary hub doc:

```json
{
  "prediction": {
    "view": "bullish",
    "horizon_days": 1,
    "expected_return_pct": 0.8,
    "range": {"low": 1285, "high": 1310},
    "confidence": 0.55,
    "provenance": {
      "direction": "debate",
      "range": "quant",
      "debate_as_of": "2026-07-16T...",
      "quant_as_of": "2026-07-16T..."
    }
  }
}
```

Debate artifact remains immutable; synthesis is a **derived layer** on the trade-plan doc.

---

## Stock quantitative predictor (new)

**Location:** `integrations/trade_integrations/dataflows/stock_research/predictor.py`

Scope (v1 — no new paid data):

- 60–120 trading days OHLCV via OpenAlgo/yfinance
- Realized vol → expected move band for 1d and `horizon_days`
- Momentum (5d/20d return) tilts `expected_return_pct` within band
- Earnings proximity from `company_research.calendar_events` widens band

Outputs mirror index predictor subset (no constituent rollup):

```python
{
  "view": "neutral",
  "expected_return_pct": 0.3,
  "range": {"low": 1280.0, "high": 1315.0},
  "horizon_days": 1,
  "model_confidence": 0.5,
  "volatility_annual_pct": 22.0,
}
```

---

## Payoff and P&L (stock)

After merge, `run_stock_research` (refactored):

1. Set `recommended.target` / `recommended.stop` from merged prediction (not fixed ±5%/−3% unless debate/quant silent).
2. `build_stock_payoff()` extended to return `max_profit`, `max_loss`, `net_max_profit`, `net_max_loss` (mirror options `estimate_strategy_metrics` pattern).
3. Propagate to `ranked_strategies[]` and `strategy_variants` in widget payload.

---

## Unified broker charges (INDmoney / OpenAlgo)

### Presets extension

**File:** `integrations/trade_integrations/dataflows/broker_charges/presets.json`

Add `statutory.nse_equity` and per-broker equity delivery/intraday flat rates (INDmoney from published pricing; Groww/Zerodha for parity).

### Calculator extension

**File:** `integrations/trade_integrations/dataflows/broker_charges/calculate.py`

New functions:

- `calculate_equity_leg_charges(leg, *, broker, product="CNC")`
- `calculate_equity_charges_for_legs(legs, *, broker, include_exit=True) -> round_trip_charges`

Deprecate inline formulas in `stock_research/payoff_charges.py`; delegate to broker_charges module.

### Broker resolution

**File:** `integrations/trade_integrations/research/broker_context.py`

```python
def resolve_broker_preset(*, openalgo_session: dict | None = None) -> str:
    # session["broker"] → TRADINGAGENTS_OPTIONS_BROKER_PRESET → presets.default_broker (indmoney)
```

Used by: stock aggregator, options payoff_charges, MCP `get_trade_charges` (fix default from zerodha → resolver), OpenAlgo `/api/trade-charges`.

---

## Widget presentability (updated gates)

Extend `trade_widgets/presentability.py` per widget-intent-gating spec:

### Stock (`stock_trade`)

- `plan_status == "ready"`
- `prediction.range.low` and `prediction.range.high` finite
- `prediction.provenance` present
- `recommended.max_profit` and `recommended.max_loss` finite (or net variants)
- `charges.round_trip_charges` finite
- `charges.per_leg` non-empty with `source == resolved broker`
- `agent_debate` fresh OR `prediction.provenance.direction == "quant"` with model_confidence ≥ 0.4

### Options (unchanged + debate)

- Existing gates plus `prediction` or `recommended` rationale references hub research timestamp
- On finalize intent: require fresh debate synthesis when `TRADINGAGENTS_REQUIRE_DEBATE_FOR_EXECUTE=true` (default true)

### Index (unchanged)

- Existing factor/scenario gates; optional debate enriches provenance only

---

## UI changes

**File:** `vibetrading/frontend/src/components/chat/TradePlanWidgetCard.tsx`

1. **Stock/index prediction strip** when `presentation_mode != options_strategy`:
   - View, horizon, expected return %, range, confidence
   - Provenance chips: "Debate", "Model"
2. **Target/stop row** on recommended block for stock
3. **`formatInr`**: treat `null`/`undefined` as "—"; use 2 decimal places for charges < ₹100
4. **Incomplete plan**: show stage checklist from `research_status` if API exposes it (phase 2)

---

## Agent / autonomous prompts

Update `execution/prompt_fragments.py` and hub context fragments:

- Before `get_*_trade_widget`: call `get_research_status` or rely on widget tool that blocks incomplete plans.
- Agent must **cite** prediction source in chat: "Debate: bullish on earnings; model: 1d range ₹1285–1310."
- Do not invent targets/charges — if widget returns `plan_status: incomplete`, report which stage is pending.

---

## Data flow (end state)

```
User / autonomous agent
  → get_stock_trade_widget(RELIANCE, refresh=true)
      → ensure_research_complete(stock)
          ├─ run_company_research (if stale)
          ├─ run_agent_debate (if stale / required)
          ├─ stock_predictor (quant band)
          ├─ debate_synthesis.merge
          ├─ rank_stock_strategies (inputs from merged prediction)
          ├─ calculate_equity_charges_for_legs (INDmoney via OpenAlgo session)
          └─ save_stock_research
      → build_stock_trade_widget_from_doc(load)
      → is_widget_presentable → SSE / MCP JSON
  → TradePlanWidgetCard renders research-filled fields
```

---

## Error handling

| Case | Behavior |
|------|----------|
| Debate running (2–5 min) | Return partial doc + `debate_pending`; no execute button |
| OpenAlgo quote unavailable | `plan_status: incomplete`; stage `live_quote` failed |
| Debate extract fails | Fall back to quant-only; provenance `direction: quant` |
| Quant history insufficient | Widen band; lower model_confidence; require debate for ready gate |
| Broker unknown | Default indmoney; log warning |

---

## Testing (implementation phase)

- Unit: registry contracts, merge rules, equity charge calc vs INDmoney published examples
- Unit: `build_stock_payoff` max P/L from target/stop
- Integration: RELIANCE orchestrator → hub JSON has prediction.provenance + charges.round_trip
- Regression: options widget unchanged when debate absent; index reconcile still works

---

## Out of scope (v1)

- Paid data vendors for equity forecasts
- Automatic debate → options leg construction (debate biases ranker only)
- Per-agent research registry overrides
- Canvas / new research UI panel (reuse Research side panel + widget)

---

## Implementation phases

| Phase | Deliverable |
|-------|-------------|
| **1** | `research/registry.py`, `orchestrator.py`, MCP wrap, hub save consistency |
| **2** | `debate_synthesis.py`, stock `predictor.py`, merge into stock aggregator |
| **3** | Equity broker_charges + stock payoff P/L + presentability gates |
| **4** | Frontend prediction strip + charge formatting |
| **5** | Options debate merge + prompt fragments + `get_research_status` MCP |

Phases 1–3 unblock RELIANCE-style stock widgets; 4–5 complete parity across asset types.

---

## References

- `docs/superpowers/specs/2026-07-16-widget-intent-gating-design.md` — presentability gates
- `docs/superpowers/specs/2026-07-16-autonomous-agents-design.md` — autonomous research loop
- `integrations/trade_integrations/dataflows/index_research/predictor.py` — quant model pattern
- `integrations/trade_integrations/dataflows/broker_charges/` — F&O charges (extend for equity)
