---
name: index-advisor
description: Index-level India market advisor — NIFTY/BANKNIFTY prediction, factor attribution, macro overlay, scenarios, and optional F&O follow-through via OpenAlgo MCP.
category: workflow
---
# Index Advisor

## Overview

Use pre-computed **index research** from the trade-stack hub for **where the index is headed and why**, then optionally hand off to **options-advisor** for actionable F&O legs.

See also: [trade-stack skill](../trade-stack/SKILL.md), [options-advisor](../options-advisor/SKILL.md) for strategy execution.

## Hub artifact

```
{{TRADE_STACK_HUB_DIR}}/{INDEX}/index_research/latest.md
{{TRADE_STACK_HUB_DIR}}/{INDEX}/index_research/latest.json
```

Supported indices: **NIFTY**, **BANKNIFTY**, **FINNIFTY**, **MIDCPNIFTY**, and other NSE index symbols in the pipeline.

Default prediction horizon: **14 days** (horizon B). Use `horizon_days=2` for 1–3 day tactical view or `horizon_days=60` for 30–90 day structural view when the user asks explicitly.

## Workflow (index view → optional options)

When the user asks where NIFTY is going, what drives the index, factor attribution, macro impact, or index scenarios:

**Automatic research (Vibe backend):** When the user mentions an index ticker (e.g. NIFTY), the session prefetches **index_research** into the Research side panel and auto-emits the **index trade widget** (factor chart). You do not need to re-fetch if the panel is fresh — but always call MCP tools when explaining so the user gets the interactive card.

### Step 1 — Load index research

1. Call OpenAlgo MCP **`get_index_trade_plan(ticker, horizon_days=14)`** (hub cache) or read `index_research/latest.json`.
2. Set `refresh=true` when the user asks for fresh research or spot moved materially.
3. For horizon-specific questions, pass explicit `horizon_days` (2 for tactical, 14 default, 60 for structural).

### Step 2 — Explain the researched index view

From the JSON, explain in plain language:

- `prediction` — view (bullish/bearish/neutral), expected return %, **range low/high**, confidence
- `factor_explanation.contributors` — top macro/constituent drivers with **contribution %** and index points
- `regime` — risk-on/off, volatility regime
- `constituent_signals` — which heavyweights pull the index up or down
- `scenarios` — event × outcome → index range with probability
- `accuracy` — recent model direction hit rate (when present)

### Step 2b — Show interactive index widget (**required** for index analysis questions)

When the user asks about index direction, factors, sensitivity, or scenarios:

1. Call OpenAlgo MCP **`get_index_trade_widget(ticker)`** (not markdown-only).
2. The tool emits a `trade_plan.widget` with `asset_type: "index"`; Vibe renders:
   - prediction range and view
   - **factor contribution chart** (`IndexFactorChart`)
   - scenario tiles
   - constituent signal summary
3. Summarize in chat: top 3 factor drivers, regime, and the most likely range into the horizon.
4. Use `refresh=true` when cache is stale or user requests fresh run.

**Wrong** — prose-only index forecast without the widget:

```
NIFTY looks bullish due to FII inflows and lower VIX. Target 25,000.
```

**Right** — call the widget in the same turn:

```
Calling get_index_trade_widget("NIFTY") — the card below shows factor attribution and the 14-day range.
USD/INR and oil are the largest macro contributors; bottom-up constituent rollup supports a modest upside bias.
```

### Step 3 — Bridge to options (when user wants to trade)

After the index view, if the user asks **what to trade** or wants legs/payoff/charges:

1. Switch to [options-advisor](../options-advisor/SKILL.md) workflow.
2. Call **`get_options_trade_widget(ticker)`** when the options hub has ranked strategies to present.
3. Reconcile index **prediction.view** with the recommended options strategy (e.g. bullish index → call spreads / bull structures).

Do **not** skip the index widget when the question is index-level, even if options are mentioned later in the same thread.

## MCP tools

| Tool | Use |
|------|-----|
| `get_index_trade_plan` | Load/generate index research (prediction, factors, scenarios) |
| `get_index_trade_widget` | **Vibe index widget** — factor chart, range, scenarios |
| `get_index_research` | LangChain/TradingAgents tool (same hub data as markdown) |
| `get_options_trade_widget` | F&O follow-through after index view |
| `run_tradingagents_analysis` | Multi-agent debate on finalize |

## Research side panel

Vibe shows hub research in the **Research** panel:

- **Trade plan** — options or stock plan when applicable
- **Index research** — prediction, factor contributors, scenarios (for NIFTY etc.)

Refer to structured hub data; do not contradict it without `refresh=true`.

## CLI regeneration

From the trade repo root:

```bash
python scripts/run_index_research.py NIFTY --horizon-days 14
python scripts/run_index_factor_snapshot.py
python scripts/run_index_calibration.py
```

## Environment

```
INDEX_RESEARCH_HORIZON_DAYS=14
TRADINGAGENTS_INDEX_PREFETCH=true
INDEX_AUTO_WIDGET_ON_PREFETCH=false
INDEX_RESEARCH_ENABLE_SCHEDULER=true
```
