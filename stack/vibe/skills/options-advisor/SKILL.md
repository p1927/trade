---
name: options-advisor
description: Event-driven India options advisor — browse chain, read hub trade plans, explain scenarios, verify payoff/charges/margin via OpenAlgo MCP, execute step-by-step after user confirmation.
category: workflow
---
# Options Advisor

## Overview

Use pre-computed **options trade plans** from the trade-stack hub plus **OpenAlgo MCP** for live chain refresh, margin, payoff validation, and basket execution.

See also: [trade-stack skill](../trade-stack/SKILL.md) for company research on stock underlyings.
For **index direction and factor attribution** before picking legs, use [index-advisor](../index-advisor/SKILL.md).

## Hub artifact

```
{{TRADE_STACK_HUB_DIR}}/{UNDERLYING}/options_research/latest.md
{{TRADE_STACK_HUB_DIR}}/{UNDERLYING}/options_research/latest.json
```

Supported underlyings: India **indices** (NIFTY, BANKNIFTY, …) and **F&O stocks** (RELIANCE, TCS, …).

## Workflow (browse → research → recommend → visualize → execute)

When the user asks what to trade, which strategy, or how to execute before expiry:

**Automatic research (Vibe backend):** When the user mentions a ticker, the session prefetches the hub trade plan and opens the **Research** side panel (Trade plan tab). Widget auto-emit is **opt-in** — only when the message intent is strategy/outlook/execute (`OPTIONS_AUTO_WIDGET_ON_PREFETCH=false` by default). Always call MCP `get_*_trade_widget` when presenting actionable strategies so the user gets the interactive card.

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

### Step 2b — Show interactive trade widget (when presenting strategy options)

When the user asks what to trade, which strategy to pick, or wants to compare ranked strategies with payoff/charges:

1. Call OpenAlgo MCP **`get_options_trade_widget(ticker)`** only when `ranked_strategies` or a recommended plan with legs is available (not for browse-only or prediction-only answers).
2. The tool persists a `trade_plan.widget` payload; Vibe chat renders it as a card with:
   - scenario tiles (agent assumptions + probability)
   - **interactive payoff chart** with adjustable strike sliders (OpenAlgo Strategy Builder component)
   - full charges (per-leg brokerage, STT, GST, round-trip)
   - recommended legs and alternatives
   - **Execute in OpenAlgo** button (user must confirm)
3. User may **drag strike sliders** in the widget, then type a follow-up (e.g. “what do you think?”). Their next chat message includes a hidden `[trade_widget_context]` block with **original vs adjusted legs** — compare your proposal to their edits and answer their question.
4. Summarize in chat: why the **recommended** tier wins vs alternatives; mention earnings/corp-event signals when present.
5. Use `refresh=true` when chain moved or user asks for fresh research.

**Wrong** — markdown-only strategy list without calling the widget:

```
Top strategies for NIFTY:
1. Iron condor (tier A, score 0.82)
2. Bull call spread (tier B, score 0.71)
Recommended: iron condor with legs 24000 PE / 24100 PE / 24500 CE / 24600 CE
```

**Right** — call `get_options_trade_widget(ticker)` in the same turn, then summarize:

```
Calling get_options_trade_widget("NIFTY") — the interactive card below shows payoff, charges, and execute steps.
Iron condor ranks first because IV is elevated and the expected range is narrow into expiry.
```

Do **not** answer strategy-comparison questions with markdown-only lists — call `get_options_trade_widget` when ranked strategies exist so the user can visualize, adjust strikes, and execute. Skip the widget for chain browse, IV regime, or event summaries without actionable legs.

### Step 3 — Validate live

- `get_strategy_payoff` and `get_trade_charges` on recommended legs
- `calculate_margin` per `implementation_steps[2]`

### Step 4 — Visual payoff

Link Strategy Builder (user must be logged into OpenAlgo):
- Payoff chart: `{meta.strategy_builder_url}` or `?plan={UNDERLYING}`
- **Live P&L over time:** `{meta.strategy_builder_pnl_url}` (`&tab=pnl`)
- **Execute wizard:** `{meta.strategy_builder_execute_url}` (`&execute=1`)

### Step 4b — Finalize with TradingAgents debate

When the user **finalizes** a plan, asks for a **second opinion**, or says **confirm / ready to trade**:

1. Call OpenAlgo MCP **`run_tradingagents_analysis(ticker)`** (or rely on auto-trigger if the **Agent debate** side panel is already loading).
2. Read the debate summary: bull/bear investment debate, risk trio, final rating.
3. **Reconcile** hub `recommended` strategy with the debate rating and risk view — state clearly where they agree or conflict.
4. Only then proceed to margin check and execution (Step 5).

The debate artifact lives at `{{TRADE_STACK_HUB_DIR}}/{UNDERLYING}/agent_debate/latest.json` and appears in the Vibe **Research → Agent debate** tab.

### Step 5 — Execute only after explicit user confirmation

**Preferred:** user clicks **Execute in OpenAlgo** on the trade widget (Vibe proxies `POST /trade/execute-basket`).

With **`OPENALGO_PAPER_MODE=true`** (default in `setup_vibe.py`), executes route to OpenAlgo **analyzer/sandbox** — use this for strategy trials before going live. Toggle live mode in OpenAlgo UI or set `OPENALGO_PAPER_MODE=false`.

**Fallback:** follow `implementation_steps` in order:
- Step 2: `calculate_margin` with step payload
- Step 4: `place_basket_order` with step payload (BUY legs first if splitting manually)

Never place live orders without clear user approval in chat.

## Active intraday paper trading (autonomous)

When the user starts autonomous paper trading, you are the sole trader until session close.

- **Goal:** maximize **risk-adjusted** paper profit by session close.
- **User is not in the loop** — no confirmation per order; `execute_auto_paper_basket` allowed.
- **You decide research depth** each scheduler turn: light check, targeted refresh, or full research — based on market feedback, P&L delta, alerts, and lifecycle (see turn prompt `research_depth_hint`).
- **Always** `record_auto_paper_decision` (ENTER/EXIT/HOLD/SKIP).
- **Tools:** all options-advisor MCP tools + auto-paper tools (`start/stop_auto_paper_trading`, `get_auto_paper_market_feedback`, `get_auto_paper_status`, `execute_auto_paper_basket`, `record_auto_paper_decision`).
- Near close: evaluate day P&L vs goal; flatten or hold as you judge best.

Start: `start_auto_paper_trading(ticker, budget_inr=..., goal=...)`.

**Stop (removes paper cron jobs):** `stop_auto_paper_trading` — agent or user. Also `POST /trade/auto-paper/stop` or `DELETE /scheduled-runs/auto-paper-agent-turn`.

Options hub-refresh jobs (`options-plan-refresh`) are separate — only run if `OPTIONS_MONITOR_ENABLE_SCHEDULER=1` in `vibetrading/agent/.env`.

**Never** live mode.

## Position review (open executed plans)

When the user asks about an **open trade**, **position P&L**, whether the thesis still holds, or a **`thesis.broken`** / superseding widget event:

1. Identify the executed widget id (`tp_*`) from chat history or the user's message.
2. Call OpenAlgo MCP **`get_plan_position_status(widget_id)`** — returns ledger entry, matched broker positions, and thesis-break reasons (empty when monitor is off).
3. Call **`get_options_trade_widget(ticker, refresh=true)`** to load the latest hub plan.
4. If the widget has **`supersedes`** and **`revision_reason`**, explain what changed vs the prior plan and how it affects the open position.
5. Compare old vs new: prediction view, recommended strategy, legs, max loss/profit, scenarios.
6. State a clear recommendation: hold, adjust strikes, or exit — with confidence and charge-aware net P&L impact.

**Never auto-execute** a replacement basket after thesis break; user must confirm in chat or via the widget Execute button.

Ledger storage: `{{TRADE_STACK_HUB_DIR}}/_data/executions/ledger.json`.

## User-adjusted legs in chat

When the user message contains `[trade_widget_context] ... [/trade_widget_context]`:

1. Read `original_legs` (your proposal) vs `user_adjusted_legs` (their widget edits) and `leg_changes`.
2. Compare risk/reward: max profit/loss, breakevens, net debit, POP if you can estimate.
3. Answer their natural-language question (e.g. “is this too wide?”, “what if spot drops?”) in light of **both** your original pick and their modification.
4. Suggest keeping, tightening, or reverting specific strikes — do not ignore the context block.


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
| `get_options_trade_widget` | **Vibe chat widget** — scenarios, payoff samples, charges, execute steps |
| `get_option_chain` | Full live chain JSON when browse needs more strikes |
| `get_strategy_payoff` | Expiry P&L curve, breakevens, PoP, net P&L |
| `get_trade_charges` | Brokerage, STT, GST, stamp, exchange, net_debit_credit |
| `calculate_margin` | Pre-trade margin check |
| `place_basket_order` | Multi-leg execution after confirm |
| `get_plan_position_status` | **Open position review** — ledger + matched positions + thesis-break (monitor-gated) |
| `run_tradingagents_analysis` | **Multi-agent debate** on finalize — bull/bear/risk, saved to hub |
| `start_auto_paper_trading` | **Start active intraday paper session** (agent-driven) |
| `stop_auto_paper_trading` | Stop paper session |
| `get_auto_paper_market_feedback` | **Every turn** — market changes, alerts, deltas |
| `get_auto_paper_status` | Session P&L, positions, funds, decisions |
| `execute_auto_paper_basket` | Enter paper trade from widget after research |
| `record_auto_paper_decision` | Log ENTER/EXIT/HOLD/SKIP each turn |

## Research side panel

Vibe shows hub research in a collapsible **Research** panel on the right:
- **Trade plan** — prediction, ranked strategies, scenarios (from hub)
- **Agent debate** — TradingAgents bull/bear/risk summary (on finalize or manual run)

Refer to what the panel shows; do not contradict structured hub data without calling `refresh=true`.

## Charges and net P&L

Always show **gross payoff**, **entry charges**, **exit charges (est.)**, **round_trip_charges**, then **net** when discussing P&L.
