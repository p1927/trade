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

1. Read `browse_summary` from `latest.json` (expiries, ATM, PCR, top strikes with LTP/OI).
2. **Refresh live** with OpenAlgo MCP `get_option_chain(underlying, exchange, expiry, strike_count=10)`.
3. Present a compact table: expiries, spot, ATM, top 5–8 strikes (CE/PE LTP + OI).
4. If user only wanted to browse, stop after summarizing the chain.

### Step 1 — Load the plan

1. Read `latest.json` (structured) or `latest.md` (summary) for the underlying.
2. **Stock options** — also read `{UNDERLYING}/company_research/latest.md` for earnings/calendar context.

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

For stock options, warm company research first when missing:

```bash
python scripts/run_company_research.py RELIANCE
python scripts/run_options_research.py RELIANCE
```

## MCP tools

| Tool | Use |
|------|-----|
| `get_option_chain` | **Browse** — live expiries, strikes, OI, LTP |
| `get_strategy_payoff` | Expiry P&L curve, breakevens, PoP, net P&L |
| `get_trade_charges` | Brokerage, STT, GST, stamp, exchange, net_debit_credit |
| `calculate_margin` | Pre-trade margin check |
| `place_basket_order` | Multi-leg execution after confirm |

## Charges and net P&L

Always show **gross payoff**, **entry charges**, **exit charges (est.)**, **round_trip_charges**, then **net** when discussing P&L.
