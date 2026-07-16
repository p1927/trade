# Phase 4 — Interactive Payoff Graph, Per-Leg Charges, Stock Advisor Parity

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let traders drag payoff-chart strike lines to update legs/orders, see per-transaction GST/brokerage/STT breakdown live, and reach stock-trading advisor parity with the options flow (browse → research → recommend → P&L/charges → execute).

**Architecture:** Extend OpenAlgo Strategy Builder (no new options UI) with `/api/trade-charges` backed by `payoff_charges.py`, draggable Plotly strike shapes wired to existing leg-edit logic, and a new `stock_research` pipeline mirroring `options_research` that consumes `company_research` hub data plus OpenAlgo quotes.

**Tech Stack:** React + Plotly (Strategy Builder), Flask blueprints, `trade_integrations` Python pipelines, OpenAlgo MCP, Vibe skills.

## Global Constraints

- No paid data vendors; India execution via OpenAlgo only.
- Do not build a separate options UI — extend Strategy Builder + Vibe chat.
- Charges must show brokerage, STT, GST, stamp, exchange per leg and in totals.
- Draggable strikes must snap to chain `strikeStep` and refresh symbol + LTP.
- Stock advisor mirrors options artifact shape: `reports/hub/{TICKER}/stock_research/latest.json`.

---

## Part A — Interactive graph + live charges (Phase 4A)

### A1 — Trade charges REST API

- [ ] **A1.1** Add `openalgo/blueprints/trade_charges.py` — `POST /api/trade-charges` with session auth; body `{ legs, spot?, broker_preset?, include_exit? }`; returns `per_leg`, `total`, `exit`, `round_trip_charges`, `net_debit_credit`.
- [ ] **A1.2** Register blueprint in `openalgo/app.py`.
- [ ] **A1.3** Wire `StrategyBuilder.tsx` — debounced fetch on `legs` change when active option legs exist; replace stale `planCharges`.

### A2 — Per-leg charge UI

- [ ] **A2.1** Extend `PositionsPanelProps.planCharges` with `per_leg[]`, `round_trip_charges`, `exit`.
- [ ] **A2.2** Render expandable per-leg rows: brokerage, STT, GST, stamp, exchange, total per transaction.
- [ ] **A2.3** Show round-trip totals when `include_exit` is used.
- [ ] **A2.4** Pass live charges from StrategyBuilder (not only on `?plan=` load).

### A3 — Draggable strike lines on payoff chart

- [ ] **A3.1** Extend `PayoffChart` props: `legs`, `strikeStep`, `onStrikeChange(legId, strike)`.
- [ ] **A3.2** Add editable vertical shapes per active OPTION leg; color CE/PE; annotate strike label.
- [ ] **A3.3** Handle `onRelayout` — snap dragged x to `strikeStep`, call `onStrikeChange`.
- [ ] **A3.4** `StrategyBuilder.handleStrikeDrag` — reuse `saveEditedLeg` logic (symbol rebuild, chain LTP sync).
- [ ] **A3.5** Invalidate `planImplementationSteps` when legs diverge from loaded plan (show "plan modified" badge).

### A4 — Verification (Phase 4A)

- [ ] **A4.1** Manual: load `?plan=NIFTY`, drag CE strike, confirm leg row + charges update.
- [ ] **A4.2** Manual: verify per-leg GST/brokerage visible for each leg.

---

## Part B — Stock trading advisor parity (Phase 4B)

### B1 — `stock_research` pipeline

- [ ] **B1.1** `integrations/.../stock_research/models.py` — `StockResearchDoc` (browse_summary, prediction, scenarios, ranked_strategies, recommended, payoff, charges, implementation_steps).
- [ ] **B1.2** `browse_summary.py` — quote, 52w range, volume, peers snapshot from company hub + OpenAlgo.
- [ ] **B1.3** `strategy_ranker.py` — candidates: buy_dip, momentum_breakout, event_play, hold_cash; score with sentiment/earnings/fundamentals.
- [ ] **B1.4** `payoff_charges.py` — CNC/MIS equity charges (brokerage, STT, stamp, GST, exchange).
- [ ] **B1.5** `aggregator.py` — load `company_research` cache → rank → recommend → charges → steps.
- [ ] **B1.6** `format.py` — agent markdown report.
- [ ] **B1.7** Hub I/O in `hub.py` — `save/load/prefetch_stock_research`.
- [ ] **B1.8** `scripts/run_stock_research.py` CLI.

### B2 — MCP + Vibe automation

- [ ] **B2.1** MCP `get_stock_browse(ticker)` — compact equity snapshot + markdown table.
- [ ] **B2.2** MCP `get_stock_trade_plan(ticker, refresh?)` — hub plan markdown.
- [ ] **B2.3** `stack/vibe/skills/stock-advisor/SKILL.md` — mirror options-advisor 5-step workflow.
- [ ] **B2.4** `scripts/setup_vibe.py` sync + `trade-stack` skill cross-link.

### B3 — Strategy Builder stock plans (stretch)

- [ ] **B3.1** Extend `/api/trade-plan` to serve `stock_research/latest.json` with `?asset=stock`.
- [ ] **B3.2** Plan loader for EQUITY legs (CNC/MIS) + stock execute wizard variant.

### B4 — Verification (Phase 4B)

- [ ] **B4.1** `python scripts/run_stock_research.py RELIANCE` → hub JSON with recommended action.
- [ ] **B4.2** Vibe: "What stock trade should I consider on RELIANCE?" → `get_stock_browse` + `get_stock_trade_plan`.

---

## Build order (this session)

1. A1 + A2 (charges API + per-leg UI) — unblocks GST/brokerage visibility immediately.
2. A3 (draggable strikes) — graph → orders.
3. B1 + B2 (stock pipeline + MCP + skill) — advisor parity core.
4. B3 deferred if time-boxed — stock Strategy Builder can follow in Phase 4B.2.

## Out of scope (Phase 5)

- Rich Vibe chat cards for browse tables.
- Full stock ExecutePlanWizard in Strategy Builder (equity CNC funds check).
- Dragging breakeven / spot lines (only strike lines in 4A).
