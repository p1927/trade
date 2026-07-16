# Widget Intent Gating — Design Spec

**Date:** 2026-07-16  
**Status:** Approved (Option A — opt-in intent-gated emission)  
**Goal:** Show trade-plan widgets in Vibe chat only when the user's intent and hub data warrant it — not on every ticker mention.

---

## Problem

Three independent paths emit `trade_plan.widget` SSE events today:

1. **Prefetch auto-emit** (`hub_bridge.py`) — defaults ON; fires when a message mentions a ticker and cached hub research has ranked strategies or index plan is ready/partial.
2. **Widget guard** (`widget_guard.py`) — defaults ON; fires when agent prose contains strategy keywords without calling an MCP widget tool.
3. **Agent MCP tools** — `get_options_trade_widget`, `get_stock_trade_widget`, `get_index_trade_widget`.

Combined with a prior "widget-by-default" design (`OPTIONS_AUTO_WIDGET_ON_PREFETCH=true`), users see FNO payoff/charges cards on browse-only questions ("What's NIFTY doing?"), educational answers, and partial/incomplete plans. Index symbols can emit **two** widgets (options + index) in one turn. The UI always renders a "Charges & costs" block even for index outlook widgets that have no F&O legs.

---

## Decision (locked)

| Decision | Choice |
|----------|--------|
| Default emission model | **Opt-in (Option A)** — prefetch loads research context always; widgets emit only on matching intent + presentable data |
| Auto-prefetch widget defaults | **`OPTIONS_AUTO_WIDGET_ON_PREFETCH=false`**, **`INDEX_AUTO_WIDGET_ON_PREFETCH=false`** |
| Intent classifier location | **`symbol_detect.py`** — shared by prefetch, widget guard, and agent prompt hints |
| Widget guard | **Tightened** — requires matching intent + presentable data; not keyword-only |
| Index vs options on same ticker | **One widget per turn** — intent picks `index_outlook` OR `options_strategy`, never both |
| UI presentation | **`presentation_mode` field** on payload — mode-specific sections (hide charges for index outlook) |
| Agent MCP tools | **Primary path** for widgets after agent turn; prefetch auto-emit only when intent + data gate pass |
| Scheduled thesis-break widgets | **Unchanged** — still emit superseding options widgets (explicit lifecycle event) |

---

## Widget intent taxonomy

```python
WidgetIntent = Literal[
    "none",              # browse, events-only, general chat — no widget
    "index_outlook",     # index direction, factors, scenario ranges
    "options_strategy",  # ranked strategies, legs, payoff, charges
    "stock_trade",       # equity buy/sell recommendation
    "execute_refresh",   # finalize / execute — refresh widget if stale
]
```

### Intent classification (message → intent)

| Pattern (regex / keywords) | Intent |
|----------------------------|--------|
| chain, OI, open interest, browse, expiries, strikes list | `none` |
| where headed, direction, outlook, factors, macro, scenario (index context) | `index_outlook` |
| strategy, iron condor, straddle, CE/PE, which option, recommend trade | `options_strategy` |
| buy/sell/hold stock, equity, shares (no options hints) | `stock_trade` |
| finalize, execute, confirm plan, ready to trade | `execute_refresh` |
| Ticker only, no trade hint | `none` |

When both index outlook and options strategy signals appear, **options_strategy wins** if options keywords present; else **index_outlook** for index-eligible tickers.

Existing helpers reused/extended:

- `detect_options_intent()` — options keyword hint
- `detect_finalize_intent()` — execute refresh
- New: `detect_index_outlook_intent()`, `detect_browse_intent()`, `classify_widget_intent(text) -> WidgetIntent`

---

## Data presentability gates

Never emit a widget (prefetch, guard, or auto) unless `is_widget_presentable(widget, intent)` returns true.

### Options (`options_strategy`, `execute_refresh`)

- `plan_status == "ready"` OR (`plan_status == "partial"` AND recommended legs exist)
- `ranked_strategies` non-empty OR `strategy_variants` non-empty with at least one variant having legs
- `payoff.samples` non-empty OR legs sufficient to compute payoff client-side
- `charges.net_debit_credit` is a finite number (charges block populated)

### Index (`index_outlook`)

- `plan_status in ("ready", "partial")`
- At least one of: `factor_explanation.contributors`, `top_factors`, `scenarios` (≥1 row)

### Stock (`stock_trade`)

- `plan_status == "ready"`
- `implementation_steps` non-empty OR `recommended` with action

---

## Emission matrix (target)

| User intent | Prefetch research | Auto-emit on prefetch | Agent MCP | `presentation_mode` |
|-------------|-------------------|----------------------|-----------|---------------------|
| `none` | Yes | No | No | — |
| `index_outlook` | Yes | Yes (if presentable) | `get_index_trade_widget` | `index_outlook` |
| `options_strategy` | Yes | Yes (if presentable) | `get_options_trade_widget` | `options_strategy` |
| `stock_trade` | Yes | Yes (if presentable) | `get_stock_trade_widget` | `stock_trade` |
| `execute_refresh` | Yes + refresh hint | Yes (if presentable) | `get_*_trade_widget(refresh=true)` | matching asset |

**Dual-widget rule:** When ticker is index-eligible and intent is `index_outlook`, skip options auto-emit. When intent is `options_strategy`, skip index auto-emit.

**Thesis-break / scheduled:** Bypass intent gate; emit superseding options widget when monitor detects thesis break (existing `options_jobs.py` path).

---

## Architecture

```
User message
  → prefetch_research_for_message()
       ├─ always: load hub + emit research.artifact SSE
       ├─ classify_widget_intent(content) → intent
       ├─ if intent == none → return context only
       ├─ if intent matches asset → build widget
       ├─ if is_widget_presentable(widget, intent) → emit trade_plan.widget
       └─ inject format_research_context_for_agent() with intent-aware MCP hints

Agent turn completes
  → widget guard: needs_widget_guard AND classify matches options_strategy
  → is_widget_presentable → maybe_inject_widget

Agent MCP tool_result
  → trade_plan_widget_frame_from_tool_result (options/stock/index)
  → SSE trade_plan.widget

Frontend Agent.tsx
  → dedupe by widget_id
  → TradePlanWidgetCard renders by presentation_mode
```

---

## Payload changes

Add to all widget builders (`widget_payload.py` ×3):

```json
{
  "presentation_mode": "options_strategy | index_outlook | stock_trade",
  "widget_intent": "options_strategy | index_outlook | stock_trade | execute_refresh"
}
```

Derived at build time from asset type + caller intent; frontend uses `presentation_mode` for section visibility.

---

## UI section visibility by `presentation_mode`

| Section | `options_strategy` | `index_outlook` | `stock_trade` |
|---------|-------------------|-----------------|---------------|
| Scenarios | Yes | Yes | Optional |
| Ranked strategies nav | Yes | No | No |
| PayoffChart (draggable) | Yes | No | No |
| IndexFactorChart | No | Yes | No |
| MiniPnlOverTimeChart | Yes | No | No |
| Charges & costs | Yes | **Hidden** | Yes (equity) |
| Execute button | Yes | No | Yes |

If required data for the mode is missing, frontend skips rendering the card (defensive; backend should already gate).

---

## Agent prompt alignment (`hub_context.py`)

Replace unconditional "MUST call widget" blocks with intent-conditional instructions injected from prefetch:

- `[widget_intent: none]` → "Do not call trade widget tools; answer from research_context."
- `[widget_intent: index_outlook]` → "Call get_index_trade_widget if not already emitted."
- `[widget_intent: options_strategy]` → "Call get_options_trade_widget when presenting legs."
- `[widget_intent: execute_refresh]` → "Call get_*_trade_widget(refresh=true) before confirm."

Skills (`options-advisor`, `index-advisor`, `stock-advisor`) updated to match.

---

## Index widget pipeline fixes (included)

1. Persist `ti_*` widgets in MCP `get_index_trade_widget` (mirror options/stock).
2. Add index tool names to `_WIDGET_TOOL_NAMES` in `trade_routes.py`.
3. Extend `_WIDGET_ID_RE` / `_WIDGET_ID_INLINE_RE` to accept `ti_` prefix.

---

## Environment variables

| Variable | Old default | New default |
|----------|-------------|-------------|
| `OPTIONS_AUTO_WIDGET_ON_PREFETCH` | `true` | **`false`** |
| `INDEX_AUTO_WIDGET_ON_PREFETCH` | `true` | **`false`** |
| `OPTIONS_WIDGET_GUARD_ENABLED` | `true` | `true` (behavior tightened, not disabled) |

Power users can set either auto-emit flag back to `true`; intent + presentability gates still apply.

---

## Out of scope (future)

- `futures_trade` presentation mode and chart (Phase 4 placeholder in implementation plan)
- Persisting widgets in chat message history for reload
- LLM-based intent classifier fallback

---

## Success criteria

1. "What's NIFTY doing?" → research context in agent reply, **no** FNO charges card unless user asked for index outlook widget path and data is presentable.
2. "Iron condor on RELIANCE" → options widget with payoff + charges.
3. "Show RELIANCE option chain" → no widget.
4. Index outlook → factor chart, **no** empty charges block.
5. NIFTY message with `index_outlook` intent → at most **one** widget (index), not options + index.
6. Agent calling `get_index_trade_widget` → card appears via SSE relay.

---

## Files touched (summary)

| Area | Files |
|------|-------|
| Intent + gates | `symbol_detect.py`, new `widget_intent.py` (or inline in symbol_detect) |
| Prefetch | `hub_bridge.py` |
| Guard | `widget_guard.py` |
| Agent context | `hub_context.py` |
| Payloads | `options_research/widget_payload.py`, `index_research/widget_payload.py`, `stock_research/widget_payload.py` |
| MCP + routes | `openalgo/mcp/mcpserver.py`, `trade_routes.py` |
| Frontend | `api.ts`, `TradePlanWidgetCard.tsx`, `Agent.tsx` |
| Skills | `stack/vibe/skills/*-advisor/SKILL.md` |
| Env examples | `.env.example`, `vibetrading/agent/.env.example` |
| Tests | `tests/test_widget_intent.py`, update `test_hub_bridge_*`, `test_widget_guard.py`, `test_hub_context.py` |
