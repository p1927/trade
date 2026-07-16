---
name: options-advisor
description: Event-driven India options advisor — browse chain, read hub trade plans, explain scenarios, verify payoff/charges/margin via OpenAlgo MCP, execute step-by-step after user confirmation.
category: workflow
---
# Options Advisor

## Overview

Use pre-computed **options trade plans** from the trade-stack hub plus **OpenAlgo MCP** for live chain refresh, margin, payoff validation, and basket execution.

See also: [trade-stack skill](../trade-stack/SKILL.md) for company research on stock underlyings.

## Hub artifact

```
{{TRADE_STACK_HUB_DIR}}/{UNDERLYING}/options_research/latest.md
{{TRADE_STACK_HUB_DIR}}/{UNDERLYING}/options_research/latest.json
```

Supported underlyings: India **indices** (NIFTY, BANKNIFTY, …) and **F&O stocks** (RELIANCE, TCS, …).

## Workflow (browse → research → recommend → visualize → execute)

When the user asks what to trade, which strategy, or how to execute before expiry:

### Step 0 — Browse what's available (always start here for "what can I trade?")

1. Call OpenAlgo MCP **`get_options_browse`** (compact table) or read `browse_summary` from `latest.json`.
2. For a full refreshed chain, use **`get_option_chain`** with `strike_count=10`.
3. Present the `markdown` field from `get_options_browse` or build a table from `browse_summary`.
4. If user only wanted to browse, stop after summarizing the chain.

### Step 1 — Load the plan

1. Call OpenAlgo MCP **`get_options_trade_plan(ticker)`** (uses hub cache) or read `latest.json` directly.
2. Set `refresh=true` on `get_options_trade_plan` when the user asks for fresh research or chain moved.
3. **Stock options** — also read `{UNDERLYING}/company_research/latest.md` for earnings/calendar context.
   - Check **Earnings Signal** and **Corp-Event Forecast** sections (Finverse + ED-ALPHA).
   - Options `latest.md` **Prediction** section mirrors those signals for strategy ranking.

### Step 2 — Explain researched answer

From the JSON, explain:
- `prediction` (view, IV regime, expected move, confidence)
- `events` and `scenarios`
- `ranked_strategies` (top 3–5 with scores/tiers)
- `recommended` (legs, rationale, gross + **net** payoff, charges, `net_debit_credit`)
- `payoff_over_time.samples` — P&L at different days-to-expiry (theta decay at current spot)

### Step 3 — Validate live

- `get_strategy_payoff` and `get_trade_charges` on recommended legs
- `calculate_margin` per `implementation_steps[2]`

### Step 4 — Visual payoff

Link Strategy Builder (user must be logged into OpenAlgo):
- Payoff chart: `{meta.strategy_builder_url}` or `?plan={UNDERLYING}`
- **Live P&L over time:** `{meta.strategy_builder_pnl_url}` (`&tab=pnl`)
- **Execute wizard:** `{meta.strategy_builder_execute_url}` (`&execute=1`)

### Step 5 — Execute only after explicit user confirmation

Follow `implementation_steps` in order:
- Step 2: `calculate_margin` with step payload
- Step 4: `place_basket_order` with step payload (BUY legs first if splitting manually)

Never place live orders without clear user approval in chat.

## Regenerate plan

From the trade repo root:

```bash
pip install -e '.[stack,options]'   # includes qfinindia, optionlab, finworth
python scripts/run_options_research.py NIFTY --expiry 30JUL25
python scripts/run_options_research.py RELIANCE --days 14
```

For stock options, warm company research first when missing (includes Finverse + ED-ALPHA for US):

```bash
python scripts/run_company_research.py RELIANCE
python scripts/run_company_research.py AAPL    # US: earnings_signal + corp_events in hub
python scripts/run_options_research.py RELIANCE
```

The options plan **reads hub signals** (`earnings_signal`, `corp_events`) into events and the strategy ranker — no need to re-fetch Finverse/ED-ALPHA during options run if company research is cached.

## MCP tools

| Tool | Use |
|------|-----|
| `get_options_browse` | **Browse in chat** — compact chain table (expiries, ATM, top strikes) |
| `get_options_trade_plan` | **Load/generate** full trade plan from hub (prediction, ranks, legs, charges) |
| `get_option_chain` | Full live chain JSON when browse needs more strikes |
| `get_strategy_payoff` | Expiry P&L curve, breakevens, PoP, net P&L |
| `get_trade_charges` | Brokerage, STT, GST, stamp, exchange, net_debit_credit |
| `calculate_margin` | Pre-trade margin check |
| `place_basket_order` | Multi-leg execution after confirm |

## Charges and net P&L

Always show **gross payoff**, **entry charges**, **exit charges (est.)**, **round_trip_charges**, then **net** when discussing P&L.
