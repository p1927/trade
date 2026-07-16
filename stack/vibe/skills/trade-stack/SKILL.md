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

Regenerate: `python scripts/run_options_research.py NIFTY`
Quick browse only: `python scripts/browse_options.py NIFTY`

## Stock advisor

For **equity** CNC/MIS trades, use the **stock-advisor** skill:

```
{{TRADE_STACK_HUB_DIR}}/{TICKER}/stock_research/latest.json
```

**Vibe automation (OpenAlgo MCP):** `get_stock_browse`; `get_stock_trade_plan`

Regenerate: `python scripts/run_stock_research.py RELIANCE`
