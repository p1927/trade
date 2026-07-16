---
name: options-advisor
description: Event-driven India options advisor — read hub trade plans, explain scenarios, verify payoff/charges/margin via OpenAlgo MCP, execute step-by-step after user confirmation.
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

## Workflow

When the user asks what to trade, which strategy, or how to execute before expiry:

1. **Read the plan** — `latest.json` (structured) or `latest.md` (summary) for the underlying.
2. **Stock options** — also read `{UNDERLYING}/company_research/latest.md` for earnings/calendar context.
3. **Live refresh** (optional) — OpenAlgo MCP `get_option_chain` for current chain vs cached snapshot.
4. **Explain** from the JSON:
   - `prediction` (view, IV regime, expected move)
   - `events` and `scenarios`
   - `ranked_strategies` (top 3–5 with scores/tiers)
   - `recommended` (legs, rationale, gross + **net** payoff, charges, `net_debit_credit`)
5. **Validate** — `get_strategy_payoff` and `get_trade_charges` on recommended legs; `calculate_margin` per `implementation_steps`.
6. **Visual payoff** — link Strategy Builder: `http://127.0.0.1:5000/strategybuilder?plan={UNDERLYING}`
7. **Execute only after explicit user confirmation** — follow `implementation_steps` in order:
   - Step 2: `calculate_margin` with step payload
   - Step 4: `place_basket_order` with step payload (BUY legs first if splitting manually)

Never place live orders without clear user approval in chat.

## Regenerate plan

From the trade repo root:

```bash
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
| `get_option_chain` | Live expiries, strikes, OI, LTP |
| `get_strategy_payoff` | Expiry P&L curve, breakevens, PoP |
| `get_trade_charges` | Brokerage, STT, GST, stamp, exchange |
| `calculate_margin` | Pre-trade margin check |
| `place_basket_order` | Multi-leg execution after confirm |

## Charges and net P&L

Always show **gross payoff** and **charges** separately, then **net** after charges when discussing P&L.
