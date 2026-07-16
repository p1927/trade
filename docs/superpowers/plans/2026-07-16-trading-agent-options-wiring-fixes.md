# Trading Agent ↔ Options Wiring Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development. Steps use checkbox syntax.

**Goal:** Close all gaps between OpenAlgo options data, Vibe chat agent, TradingAgents debate, and trade-plan widgets so NIFTY/F&O flows show ranked strategies, scenario switching, P&L-over-time, and agent-visible chain failures.

**Architecture:** Sync hub prefetch before the first agent turn and inject `[research_context]` into the LLM prompt; auto-refresh incomplete hub cache; enrich scenario generation from ranked strategies; add strategy carousel + client-side theta P&L in `TradePlanWidgetCard`; inject options hub markdown into TradingAgents `past_context` for index/options runs; infer `asset_type=options` for index tickers in MCP debate tool.

**Tech Stack:** Python `trade_integrations`, `vibetrading/agent`, OpenAlgo MCP, React `TradePlanWidgetCard`.

## Global Constraints

- India execution/data via OpenAlgo only; no new paid vendors.
- Minimal diffs; match existing hub/MCP/widget patterns.
- User-requested plan doc; tests only where listed per task.

---

### Task 1: Hub research context injection + prefetch-before-agent

**Files:**
- Modify: `vibetrading/agent/src/trade/hub_bridge.py`
- Modify: `vibetrading/agent/src/session/service.py`

- [x] Add `format_research_context_for_agent(artifact)` → `[research_context]` block with plan_status, warnings, ranked strategies, recommended, stage_errors.
- [x] Refactor `prefetch_research_for_message()` to return context string; keep SSE emit.
- [x] `send_message`: `await asyncio.to_thread(prefetch...)` **before** `_run_attempt`; pass context into `_run_with_agent` and prefix `user_message`.

---

### Task 2: Incomplete hub cache auto-refresh

**Files:**
- Modify: `integrations/trade_integrations/tools/options_research_tools.py`
- Modify: `integrations/trade_integrations/dataflows/options_research/widget_payload.py`

- [x] Skip stale cache when `recommended.name` empty or chain stage error.
- [x] `build_options_trade_widget`: force `refresh=True` when cached doc is `incomplete`.

---

### Task 3: Distinct scenario generation from ranked strategies

**Files:**
- Modify: `integrations/trade_integrations/dataflows/options_research/strategy_ranker.py`
- Test: `tests/test_strategy_scenarios.py`

- [x] Rewrite `build_scenarios()` to emit up to 4 archetypes (base, bullish, bearish, high-vol) mapped to **distinct** ranked strategy names.

---

### Task 4: Widget strategy carousel + scenario/variant polish

**Files:**
- Modify: `vibetrading/frontend/src/components/chat/TradePlanWidgetCard.tsx`
- Modify: `vibetrading/frontend/src/lib/tradePlanLegs.ts`
- Modify: `integrations/trade_integrations/dataflows/options_research/widget_payload.py`

- [x] Normalize strategy hint keys (snake_case aliases in `strategy_variants`).
- [x] Add prev/next strategy carousel over `ranked_strategies`.
- [x] `selectedStrategy` state; sync with scenario tiles.
- [x] Client-side `computePnlOverTimeSamples()` when variant samples missing.
- [x] P&L chart empty-state message; show max P/L when ranked row exists without `rec.name`.

---

### Task 5: TradingAgents options context + MCP asset_type

**Files:**
- Create: `integrations/trade_integrations/bridge/hub_context.py`
- Modify: `integrations/trade_integrations/bridge/agent_debate.py`
- Modify: `integrations/trade_integrations/register.py`
- Modify: `openalgo/mcp/mcpserver.py`

- [x] `build_tradingagents_options_context(ticker, asset_type)` from hub markdown.
- [x] Inject into graph `past_context` at debate start for options/index runs.
- [x] `run_tradingagents_analysis`: infer `asset_type=options` for NIFTY/BANKNIFTY/F&O names.
- [x] `hub_bridge.run_agent_debate_sync`: pass inferred asset_type for indices.

---

### Task 6: Verification

- [x] `pytest tests/test_strategy_scenarios.py tests/test_hub_context.py tests/test_widget_payload.py -q`
- [ ] Manual: NIFTY chat → agent sees research_context → widget shows 4 scenarios + carousel
