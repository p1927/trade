---
name: quant-reviewer
description: India NIFTY quant second opinion — surprises, TA consensus, and labeled disagreements with Ridge forecast (read-only).
category: workflow
---
# Quant Reviewer (India)

## Overview

Separate **second opinion** from the Ridge index forecast. Use when the user asks "what am I missing?", "blind spots", or wants TA/flow context without overriding the model headline.

Artifact: `{{TRADE_STACK_HUB_DIR}}/{INDEX}/quant_review/latest.json`

## When to use

- User asks for surprises, disagreements, or TA interpretation vs model
- Weekly post-close review (on-demand, not every refresh)
- Before options handoff when model view and TA diverge

## Workflow

### Step 1 — Load or refresh review

1. Call OpenAlgo MCP **`run_quant_review(ticker, horizon_days=14)`** when cache stale or user requests fresh scan.
2. Or read cached via **`get_index_quant_review`** / hub JSON.

### Step 2 — Explain (labeled separate from forecast)

From `quant_review/latest.json`:

- `model_prediction_view` + `model_expected_return_pct` — **Ridge baseline** (cite first)
- `ta_consensus` — scanner TA direction (not the headline forecast)
- `active_strategy_profile` — playbook profile (momentum, mean_reversion, flow_driven, …)
- `technical_interpretation` — one-paragraph agent-readable TA summary
- `disagreements_with_forecast[]` — explicit conflicts with Ridge
- `surprises[]` — actionable blind spots
- `disclaimer` — always show: reviewer opinion, separate from Ridge

### Step 3 — Optional swarm deep-dive

For richer narrative, `run_swarm(prompt="...", preset_name="india_quant_reviewer", variables={target, horizon_days})`.

Rule-based `run_quant_review` is the default — swarm is optional depth.

## MCP tools

| Tool | Use |
|------|-----|
| `run_quant_review` | Build + save `quant_review/latest.json` |
| `get_index_trade_plan` | Ridge baseline for comparison |
| `get_index_trade_widget` | Show factor chart alongside review |

## Do not

- Override or restate Ridge `prediction` as your own forecast
- Place orders — OpenAlgo execution stays in options-advisor / ExecutePlanWizard
- Run on every light_refresh tick — weekly or on-demand only
