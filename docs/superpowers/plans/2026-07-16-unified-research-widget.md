# Unified Research → Widget Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate all trade-plan widgets on completed research; merge TradingAgents debate + quantitative models into hub artifacts; unify INDmoney/OpenAlgo broker charges for options and equity.

**Architecture:** `research/registry.py` defines per-asset contracts; `research/orchestrator.py` runs required stages and saves hub JSON before widget build; `research/debate_synthesis.py` merges debate + quant prediction; stock `predictor.py` supplies range bands; `broker_charges` extended for equity CNC/MIS.

**Tech Stack:** Python 3.11+, existing hub (`context/hub.py`), TradingAgents debate, OpenAlgo MCP, React TradePlanWidgetCard.

## Global Constraints

- **Prediction authority:** Hybrid C — debate direction/catalysts; quant model range bands; merged with `prediction.provenance`.
- **No widget without orchestrator:** MCP `get_*_trade_widget` calls `ensure_research_complete` then loads hub JSON.
- **Charges broker:** OpenAlgo session `broker` → `TRADINGAGENTS_OPTIONS_BROKER_PRESET` → `indmoney` default.
- **Hub is source of truth:** Widget builders read saved hub doc; orchestrator saves after complete run.
- **Debate required for stock execute/finalize** when `TRADINGAGENTS_REQUIRE_DEBATE_FOR_EXECUTE=true` (default true).
- **No paid data vendors;** yfinance/OpenAlgo for OHLCV.
- **User rule:** No extra docs/tests beyond plan tasks.

---

### Task 1: Research registry module

**Files:**
- Create: `integrations/trade_integrations/research/__init__.py`
- Create: `integrations/trade_integrations/research/registry.py`
- Test: `tests/test_research_registry.py`

**Interfaces:**
- Produces: `ResearchKind`, `ResearchStage`, `ResearchKindContract`, `get_contract(kind)`, `resolve_kind_for_ticker(ticker) -> ResearchKind | None`

- [ ] **Step 1:** Write tests for options/stock/index contracts (stage ids, hub subdirs, required fields).
- [ ] **Step 2:** Implement registry with eligibility hooks from existing `is_*_research_eligible` helpers.
- [ ] **Step 3:** Run `pytest tests/test_research_registry.py -v`
- [ ] **Step 4:** Commit

---

### Task 2: Research orchestrator (stage runner + hub save)

**Files:**
- Create: `integrations/trade_integrations/research/orchestrator.py`
- Modify: `integrations/trade_integrations/dataflows/stock_research/widget_payload.py`
- Modify: `integrations/trade_integrations/dataflows/options_research/widget_payload.py`
- Modify: `integrations/trade_integrations/dataflows/index_research/widget_payload.py`
- Test: `tests/test_research_orchestrator.py`

**Interfaces:**
- Consumes: `get_contract`, existing `run_*_research`, hub save/load
- Produces: `ResearchResult`, `ensure_research_complete(ticker, *, kind, refresh=False, horizon_days=14, require_debate=None) -> ResearchResult`

- [ ] **Step 1:** Write tests: orchestrator calls aggregator, saves hub, returns complete/incomplete status.
- [ ] **Step 2:** Implement orchestrator (batch stages only in v1; debate hook placeholder).
- [ ] **Step 3:** Wire `build_*_trade_widget` to orchestrator + load from hub.
- [ ] **Step 4:** Run tests + `pytest tests/test_stock_widget_payload.py -v`
- [ ] **Step 5:** Commit

---

### Task 3: Stock quantitative predictor

**Files:**
- Create: `integrations/trade_integrations/dataflows/stock_research/predictor.py`
- Test: `tests/test_stock_predictor.py`

**Interfaces:**
- Produces: `predict_stock(ticker, spot, *, horizon_days=14) -> dict` with view, expected_return_pct, range, model_confidence

- [ ] **Step 1:** Write tests with fixed OHLCV fixture.
- [ ] **Step 2:** Implement predictor (realized vol band + momentum tilt).
- [ ] **Step 3:** Run tests
- [ ] **Step 4:** Commit

---

### Task 4: Debate synthesis + merge into stock doc

**Files:**
- Create: `integrations/trade_integrations/research/debate_synthesis.py`
- Modify: `integrations/trade_integrations/dataflows/stock_research/aggregator.py`
- Test: `tests/test_debate_synthesis.py`

**Interfaces:**
- Consumes: `load_agent_debate_json`, `predict_stock`
- Produces: `extract_structured_debate`, `merge_stock_prediction`, orchestrator calls after debate stage

- [ ] **Step 1:** Write tests for merge rules (debate direction, quant range, provenance).
- [ ] **Step 2:** Implement synthesis + wire into orchestrator/stock aggregator.
- [ ] **Step 3:** Run tests
- [ ] **Step 4:** Commit

---

### Task 5: Equity broker charges + stock payoff P/L

**Files:**
- Modify: `integrations/trade_integrations/dataflows/broker_charges/presets.json`
- Modify: `integrations/trade_integrations/dataflows/broker_charges/calculate.py`
- Modify: `integrations/trade_integrations/dataflows/stock_research/payoff_charges.py`
- Create: `integrations/trade_integrations/research/broker_context.py`
- Test: `tests/test_equity_broker_charges.py`

**Interfaces:**
- Produces: `calculate_equity_charges_for_legs`, `resolve_broker_preset()`, `build_stock_payoff` returns max_profit/max_loss/net variants

- [ ] **Step 1:** Extend presets with equity statutory + INDmoney delivery rates.
- [ ] **Step 2:** Implement equity charge calc + round_trip_charges.
- [ ] **Step 3:** Update stock aggregator to use merged target/stop and new payoff/charges.
- [ ] **Step 4:** Run tests
- [ ] **Step 5:** Commit

---

### Task 6: Presentability gates + MCP broker default fix

**Files:**
- Modify: `integrations/trade_integrations/trade_widgets/presentability.py`
- Modify: `openalgo/mcp/mcpserver.py` (get_trade_charges default)
- Test: `tests/test_stock_widget_presentability.py`

- [ ] **Step 1:** Tighten `_stock_presentable` per spec.
- [ ] **Step 2:** Align MCP get_trade_charges broker default with resolver.
- [ ] **Step 3:** Run widget + presentability tests
- [ ] **Step 4:** Commit

---

### Task 7: Frontend prediction strip + charge formatting

**Files:**
- Modify: `vibetrading/frontend/src/components/chat/TradePlanWidgetCard.tsx`

- [ ] **Step 1:** Stock/index prediction block (view, range, provenance).
- [ ] **Step 2:** Target/stop row; formatInr null→—; 2dp for charges under ₹100.
- [ ] **Step 3:** Commit

---

### Task 8: Options/index debate merge + research status MCP + prompts

**Files:**
- Modify: `integrations/trade_integrations/research/debate_synthesis.py` (options/index merge)
- Modify: `integrations/trade_integrations/execution/prompt_fragments.py`
- Modify: `openalgo/mcp/mcpserver.py` (`get_research_status` tool)
- Test: extend `tests/test_debate_synthesis.py`

- [ ] **Step 1:** Options ranker bias + index reconcile hooks.
- [ ] **Step 2:** MCP `get_research_status` + prompt fragments.
- [ ] **Step 3:** Run tests
- [ ] **Step 4:** Commit

---

## Verification (end-to-end)

```bash
pytest tests/test_research_registry.py tests/test_research_orchestrator.py \
  tests/test_stock_predictor.py tests/test_debate_synthesis.py \
  tests/test_equity_broker_charges.py tests/test_stock_widget_payload.py -v

python3 -c "
from integrations.trade_integrations.research.orchestrator import ensure_research_complete
from integrations.trade_integrations.research.registry import ResearchKind
r = ensure_research_complete('RELIANCE', kind=ResearchKind.STOCK, refresh=True, horizon_days=1)
print(r.status, r.doc.prediction if r.doc else None)
"
```
