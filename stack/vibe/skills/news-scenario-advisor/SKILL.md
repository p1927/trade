---
name: news-scenario-advisor
description: News-driven NIFTY what-if advisor on the Prediction tab — bound to the Analysis pipeline snapshot, outcome branches, quant paths, scenario widgets.
category: workflow
---
# News Scenario Advisor

## Overview

Help the user explore **what-if outcomes from news** on NIFTY using the **frozen Analysis pipeline snapshot** (same factors, Ridge equation, constituents, embedded headlines as the main Prediction run).

You do **not** refresh index research or fetch new hub data. Session config includes `pipeline_as_of` — pass it to every pipeline MCP tool.

See also: [index-advisor](../index-advisor/SKILL.md) for factor vocabulary.

## Workflow

1. **`get_pipeline_snapshot(ticker, pipeline_as_of)`** — confirm binding; summarize spot and baseline prediction.
2. Ask for **date range** (start/end) if the user has not provided one.
3. **`get_pipeline_news_items(...)`** — list headlines from the snapshot; user may pick one or define a **custom event**.
4. Propose **2–4 outcomes** (e.g. escalation / status quo / de-escalation) with intensity and factor rationale.
5. Use **`query_factor_sensitivity`**, **`get_playground_context`**, **`query_constituent_drivers`** to map shocks — do not guess factor impacts.
6. **`save_news_scenario_draft`** — persist event + outcomes JSON (include `date_range`, `factor_overrides`, `primary_factor`).
7. When the user wants to see paths: **`run_news_event_scenario`** then **`get_news_scenario_widget`** (same turn).
8. Explain results using tool-returned numbers only; cite top **`contributors`** per outcome.

## Outcome draft shape

Each outcome needs at minimum:

- `id`, `label`, `intensity` (`low`|`medium`|`high`)
- `primary_factor` from macro catalog (e.g. `oil_brent`, `india_vix`, `fii_net_5d`)
- `factor_overrides` as percent strings (e.g. `{"oil_brent": "+10%"}`) when known

## Widget rule

When the user says show, predict, chart, or selects an outcome → **must** call `get_news_scenario_widget` so the canvas updates.

## Wrong vs right

**Wrong** — invent Nifty levels without tools:

```
If conflict escalates NIFTY could fall to 23,500.
```

**Right** — quant tools first:

```
Calling run_news_event_scenario for your three branches… [widget shows baseline vs outcome paths]
```
