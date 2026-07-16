---
name: trade-stack
description: Trade stack context — company research dossiers from TradingAgents pipeline, shared hub reports, and live Indian broker execution via OpenAlgo MCP.
category: workflow
---
# Trade Stack Integration

## Overview

This workspace runs a layered trade stack:

1. **Company research pipeline** (`integrations/trade_integrations`) — identity, calendar, live NSE/BSE context
2. **TradingAgents** — batch multi-agent analysis (reports under `results_dir`)
3. **OpenAlgo MCP** (`openalgo` server in agent.json) — live quotes, positions, orders for Indian brokers

Always prefer **OpenAlgo MCP** for live Indian execution (Groww, INDmoney, etc.). Vibe's native Dhan/Shoonya connectors are read-only/paper only.

## Hub-first data discipline (avoid duplicate API calls)

The shared hub at `{{TRADE_STACK_HUB_DIR}}` is the **single store** for research, history, and calibration. Vibe and TradingAgents both read from it — do not re-fetch upstream vendors when hub data is fresh.

**Read order:**

1. **Research panel / `[research_context]`** — auto-prefetched on ticker mention
2. **OpenAlgo MCP** — `get_*_browse`, `get_*_trade_plan`, `get_*_widget` with **`refresh=false`** unless the user asks to regenerate
3. **`run_tradingagents_analysis(refresh=false)`** — debate synthesis only on finalize / second opinion
4. **Avoid** — `get_market_data`, `get_stock_news`, or raw vendor calls when hub `latest.json` / MCP browse already covers the symbol

**Long-term repository:** `_data/index_factors/`, `_data/*_predictions/ledger.parquet`, `_data/news/daily/`, `_data/derivatives_chain/daily/`, `_data/ticks/daily/` (exported from Timescale), `{TICKER}/company_research/history/` feed model retrain and ranker calibration. Inventory: `python scripts/hub_inventory.py`. Unified nightly calibration: `python scripts/run_hub_calibration.py --phase all`. Cross-ledger SQL: `python scripts/hub_query.py --list-builtins`.

**What lives where:** `latest.json` = live working copy (TTL refresh). Hub parquet/json history = training + replay. TimescaleDB = hot sub-minute ticks during watch only (optional; `TIMESCALE_ENABLED=true`).

## Shared context hub

Pre-computed research lives at:

```
{{TRADE_STACK_HUB_DIR}}/{TICKER}/company_research/latest.md
{{TRADE_STACK_HUB_DIR}}/{TICKER}/company_research/latest.json
```

When the user asks about a stock:

1. Read `latest.md` from the hub for that ticker (use file tools; hub root is allowlisted).
   - **Earnings Signal** (Finverse beat %) and **Corp-Event Forecast** (ED-ALPHA) sections inform buy/sell and event-risk views.
2. If missing or stale, note that the user can run `python scripts/run_company_research.py TICKER`.
3. Use **OpenAlgo MCP** tools for live quotes, positions, and order placement.

## Trading workflow

1. **Research** — load hub dossier + ask OpenAlgo MCP for live quote/positions.
2. **Plan** — backtest or outline the trade; state assumptions clearly.
3. **Confirm** — never place live orders without explicit user approval in chat.
4. **Execute** — use OpenAlgo MCP `place_order` (or equivalent) only after confirmation.

## India tickers

- NSE equities: `RELIANCE`, `RELIANCE.NS`
- Indices (`NIFTY`, `^NSEI`) — use OpenAlgo for data; company research hub does not apply.

## Regenerate research

From the trade repo root:

```bash
python scripts/run_company_research.py RELIANCE --days 14
python scripts/run_nifty_analysis.py   # index multi-agent run
```

## Options advisor

For index or F&O stock options, use the **options-advisor** skill and hub path:

```
{{TRADE_STACK_HUB_DIR}}/{UNDERLYING}/options_research/latest.json
```

**Vibe automation (OpenAlgo MCP):** `get_options_browse` for in-chat chain tables; `get_options_trade_plan` to load or refresh the full plan.

On **finalize**, call `run_tradingagents_analysis` — debate appears in the Research side panel (Agent debate tab).

Regenerate: `python scripts/run_options_research.py NIFTY`
Quick browse only: `python scripts/browse_options.py NIFTY`

## Stock advisor

For **equity** CNC/MIS trades, use the **stock-advisor** skill:

```
{{TRADE_STACK_HUB_DIR}}/{TICKER}/stock_research/latest.json
```

**Vibe automation (OpenAlgo MCP):** `get_stock_browse`; `get_stock_trade_plan`

Regenerate: `python scripts/run_stock_research.py RELIANCE`

## Index advisor

For **index-level** prediction, factor attribution, macro overlay, and scenarios (before or instead of F&O legs), use the **index-advisor** skill:

```
{{TRADE_STACK_HUB_DIR}}/NIFTY/index_research/latest.json
```

**Vibe automation (OpenAlgo MCP):** `get_index_trade_plan` (accepts `horizon_days`); **`get_index_trade_widget`** for the factor chart card.

When the user mentions NIFTY/BANKNIFTY, Vibe auto-prefetches index research and emits the index widget. For strategy legs afterward, use **options-advisor**.

Regenerate: `python scripts/run_index_research.py NIFTY --horizon-days 14`
Daily factors: `python scripts/run_index_factor_snapshot.py`
