---
name: stock-advisor
description: Event-driven India stock advisor — browse equity, read hub trade plans, explain scenarios, verify charges via OpenAlgo MCP, execute step-by-step after user confirmation.
category: workflow
---
# Stock Advisor

## Overview

Use **stock trade plans** from the trade-stack hub plus **OpenAlgo MCP** for live quotes, funds check, and CNC order execution.

See also: [trade-stack skill](../trade-stack/SKILL.md) and [options-advisor](../options-advisor/SKILL.md) for F&O.

## Hub artifact

```
{{TRADE_STACK_HUB_DIR}}/{TICKER}/stock_research/latest.json
{{TRADE_STACK_HUB_DIR}}/{TICKER}/company_research/latest.json  (research input)
```

## Workflow (browse → research → recommend → charges → execute)

### Step 0 — Browse

1. Call MCP **`get_stock_browse(ticker)`** for price, sector, 52w range, peers.
2. Present the `markdown` field to the user.

### Step 1 — Load plan

1. Call **`get_stock_trade_plan(ticker)`** (hub cache) or `refresh=true` for fresh research.
2. Optionally read `company_research/latest.md` for full dossier depth.

### Step 2 — Explain

From JSON, explain:
- `prediction` (view, horizon, confidence)
- `events` and `scenarios`
- `ranked_strategies` (buy_dip, momentum_breakout, event_play, hold_cash)
- `recommended` (entry, target, stop, action)
- `charges.per_leg` — brokerage, STT, GST, stamp per transaction

### Step 3 — Validate live

- `get_quote` for current price vs plan entry
- `get_funds` before CNC buy

### Step 4 — Execute after explicit confirmation

Follow `implementation_steps`:
- Step 4: `place_order` with CNC payload from the plan

Never place live orders without clear user approval.

## Regenerate

```bash
python scripts/run_stock_research.py RELIANCE --days 14
```

## MCP tools

| Tool | Use |
|------|-----|
| `get_stock_browse` | In-chat equity snapshot |
| `get_stock_trade_plan` | Full stock trade plan |
| `get_quote` | Live price refresh |
| `get_funds` | Cash available for CNC |
| `place_order` | Single equity execution |
