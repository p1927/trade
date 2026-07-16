# Vibe Trade Widgets — Optional Features + Paper Trading

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Vibe chat trade-plan widget experience: scenario-aware strategy switching, theta P&L chart, stock-equity widget parity, and safe paper execution via OpenAlgo analyzer/sandbox before live orders.

**Architecture:** Extend existing `trade_plan.widget` JSON (options + stock) with `strategy_variants` keyed by strategy name; frontend `TradePlanWidgetCard` selects variants when scenario tiles change. Add `MiniPnlOverTimeChart` for `payoff_over_time.samples`. Mirror `widget_payload.py` for `stock_research`. Paper mode: `OPENALGO_PAPER_MODE` env drives Vibe `trade_routes` to ensure OpenAlgo analyzer mode (`POST /api/v1/analyzer/toggle`) before `basketorder`; UI shows Paper/Live badge.

**Tech Stack:** Python `trade_integrations`, OpenAlgo MCP/REST sandbox, Vibe FastAPI + React/ECharts, existing Strategy Builder deep links.

## Global Constraints

- India execution via OpenAlgo only; no new paid data vendors.
- Do not build a separate options UI — widgets live in Vibe chat; Strategy Builder remains deep-link target.
- All costs visible: per-leg brokerage, STT, GST, round-trip, net P&L.
- Paper trading uses OpenAlgo **analyzer mode** + sandbox DB (existing `openalgo/sandbox/`).
- Never place live orders without explicit user confirmation in widget dialog.
- User rule: no extra docs/tests/demo unless listed in this plan.

---

## Phase 0 — Baseline commit (present widget work)

- [x] Vibe frontend SSE + `TradePlanWidgetCard` + execute proxy
- [x] `get_options_trade_widget` MCP + `widget_payload.py`
- [x] `options-advisor` skill + `setup_vibe.py` OpenAlgo env

**Commit:** `feat: add Vibe trade-plan widgets with OpenAlgo execute proxy`

---

## Phase 1 — Scenario-aware strategy switching

**Files:**
- Modify: `integrations/trade_integrations/dataflows/options_research/widget_payload.py`
- Modify: `integrations/trade_integrations/dataflows/stock_research/widget_payload.py` (Phase 3)
- Modify: `vibetrading/frontend/src/components/chat/TradePlanWidgetCard.tsx`
- Modify: `vibetrading/frontend/src/lib/api.ts`
- Test: `tests/test_widget_payload.py`

**Interfaces:**
- Produces: `strategy_variants: Record<string, { recommended, payoff, charges, payoff_over_time, implementation_steps }>`
- Consumes: `scenarios[].strategy_hint` → lookup in `strategy_variants`

- [ ] **1.1** Add `_strategy_variant(rank_row)` helper; populate `strategy_variants` for top 5 ranked strategies (legs, payoff samples, charges, steps).
- [ ] **1.2** Extend `TradePlanWidget` type with `strategy_variants`.
- [ ] **1.3** On scenario tile click: resolve `strategy_hint` → variant; update displayed recommended/payoff/charges/execute orders; highlight if scenario differs from agent recommendation.
- [ ] **1.4** Test: widget payload contains variants keyed by strategy name.

---

## Phase 2 — P&L over time mini chart

**Files:**
- Create: `vibetrading/frontend/src/components/charts/MiniPnlOverTimeChart.tsx`
- Modify: `vibetrading/frontend/src/components/chat/TradePlanWidgetCard.tsx`

- [ ] **2.1** Chart: x = `days_to_expiry`, y = `net_pnl ?? pnl`; reuse `getChartTheme` + echarts pattern from `MiniPayoffChart`.
- [ ] **2.2** Show below expiry payoff when `payoff_over_time.samples.length >= 2`; label "P&L vs days to expiry (at current spot)".
- [ ] **2.3** Scenario switch updates `payoff_over_time` from selected variant.

---

## Phase 3 — Stock trade widget parity

**Files:**
- Create: `integrations/trade_integrations/dataflows/stock_research/widget_payload.py`
- Modify: `openalgo/mcp/mcpserver.py` — `get_stock_trade_widget`
- Modify: `vibetrading/agent/src/api/trade_routes.py` — `ts_` widget IDs, stock MCP tool names, single-leg basket execute
- Modify: `stack/vibe/skills/stock-advisor/SKILL.md`
- Test: `tests/test_stock_widget_payload.py`

- [ ] **3.1** `build_stock_trade_widget_from_doc` — same shape as options widget (`asset_type: "stock"`).
- [ ] **3.2** MCP tool persists to `~/.vibe-trading/trade_widgets/{widget_id}.json`.
- [ ] **3.3** SSE relay accepts `get_stock_trade_widget` / `mcp_openalgo_get_stock_trade_widget`.
- [ ] **3.4** `TradePlanWidgetCard`: show "Stock" vs "Options" badge from `instrument_type` / `asset_type`.
- [ ] **3.5** Stock-advisor skill: prefer `get_stock_trade_widget` for equity trade questions.

---

## Phase 4 — OpenAlgo paper trading

**Files:**
- Modify: `vibetrading/agent/src/api/trade_routes.py`
- Modify: `scripts/setup_vibe.py`
- Modify: `.env.example`
- Modify: `vibetrading/frontend/src/lib/api.ts`, `TradePlanWidgetCard.tsx`
- Modify: `stack/vibe/skills/options-advisor/SKILL.md`, `stock-advisor/SKILL.md`

**OpenAlgo behavior (existing):** When `get_analyze_mode()` is true, `basketorder` routes to `sandbox_place_order` (paper fills).

- [ ] **4.1** Env `OPENALGO_PAPER_MODE=true|false` (default `true` for local dev safety).
- [ ] **4.2** `GET /trade/execution-mode` — query OpenAlgo `POST /api/v1/analyzer` → `{ mode: "paper"|"live", analyze_mode: bool }`.
- [ ] **4.3** `execute_basket`: if `OPENALGO_PAPER_MODE=true`, call `/api/v1/analyzer/toggle` with `mode: true` before basket (idempotent).
- [ ] **4.4** Widget meta `execution_mode` + frontend Paper badge; execute button "Execute (Paper)" vs "Execute (Live)".
- [ ] **4.5** Skills: recommend paper mode for first-time strategy trials; mention OpenAlgo Analyzer UI toggle.

---

## Phase 5 — Verification

- [ ] **5.1** `pytest tests/test_widget_payload.py tests/test_stock_widget_payload.py -q`
- [ ] **5.2** `python scripts/setup_vibe.py --dry-run` includes `OPENALGO_HOST`, `OPENALGO_API_KEY`, `OPENALGO_PAPER_MODE`
- [ ] **5.3** Manual: Vibe chat → options widget → switch scenario → payoff/charges update
- [ ] **5.4** Manual: Execute with `OPENALGO_PAPER_MODE=true` → response `mode: analyze` in OpenAlgo

---

## Deferred (out of scope)

- Draggable strikes in Strategy Builder (see `2026-07-16-phase4-interactive-graph-stock-advisor.md` Part A)
- Stock Strategy Builder execute wizard (Part B3 stretch)
- ED-ALPHA GDELT batch ingest for corp-event scores
