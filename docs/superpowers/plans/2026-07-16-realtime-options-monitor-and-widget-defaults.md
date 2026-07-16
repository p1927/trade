# Real-Time Options Monitor + Widget-by-Default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep options trade plans current as live news and market data move, detect when an open position no longer matches the thesis, surface re-recommendations in chat, and ensure the agent always renders the interactive trade widget (not markdown-only) when presenting options strategies.

**Architecture:** Two **independent** subsystems:

1. **`trade_integrations.monitor` (opt-in real-time)** — A self-contained package behind `OPTIONS_REALTIME_MONITOR_ENABLED=false` (default off). When enabled, a `MonitorService` runs periodic polls (`OPTIONS_MONITOR_POLL_CRON`) and news-driven checks (`news_watcher`) without modifying the core `run_options_research()` pipeline. Hub bridge and scheduled jobs call `MonitorService` only through its public API (`is_enabled()`, `evaluate()`, `maybe_refresh()`). Disabled = zero monitor side-effects.

2. **Widget-by-default (always on)** — Separate from the monitor. Auto-emit + mandatory agent contract; controlled by `OPTIONS_AUTO_WIDGET_ON_PREFETCH=true` only.

The monitor emits SSE (`plan.stale`, `plan.updated`, `thesis.broken`) and superseding widgets when enabled. Execution ledger lives inside `monitor/` but is imported by `trade_routes` only when recording executions (ledger writes are harmless when monitor disabled; reads gated by `is_enabled()`).

**Tech Stack:** Python `trade_integrations` + `vibetrading/agent`, OpenAlgo MCP/REST (quotes, positions, chain), existing `news_aggregator`, Vibe SSE + `TradePlanWidgetCard`, React polling hook (no new paid vendors).

## Global Constraints

- India options data and execution via **OpenAlgo** only; free/open-source data sources only.
- Hub artifacts remain the source of truth: `reports/hub/{SYMBOL}/options_research/latest.json`.
- Widgets live in **Vibe chat**; Strategy Builder stays a deep-link target — do not fork a second options UI.
- All P&L and charges must stay visible in widget (gross + net, per-leg breakdown).
- Live orders only after explicit user confirmation in widget dialog.
- Re-recommendation is **advisory** — never auto-execute basket orders.
- No new summary docs, demos, or mocks beyond tasks listed here.

---

## Current State (baseline)

| Capability | Status | Key files |
|------------|--------|-----------|
| Batch options research → hub | **Done** | `integrations/trade_integrations/dataflows/options_research/aggregator.py` |
| Hub prefetch on ticker mention | **Done** | `vibetrading/agent/src/trade/hub_bridge.py` |
| Interactive widget in chat | **Done** | `widget_payload.py`, `TradePlanWidgetCard.tsx`, MCP `get_options_trade_widget` |
| File-mtime cache TTL (30 min default) | **Partial** | `integrations/trade_integrations/context/hub.py` |
| Live P&L in Strategy Builder | **Done** | `openalgo/frontend/src/hooks/useMarketData.ts` |
| News at pipeline run time | **Batch only** | `integrations/trade_integrations/dataflows/news_aggregator/` |
| Position ↔ plan linkage | **Missing** | `vibetrading/agent/src/api/trade_routes.py` |
| Thesis-break re-recommendation | **Missing** | — |
| Widget mandatory on strategy answers | **Skill-only** | `stack/vibe/skills/options-advisor/SKILL.md` |

---

## Target End-to-End Flow

```
User mentions RELIANCE (or has open position)
  → hub_bridge prefetch + auto-emit trade_plan.widget
  → plan_monitor.evaluate(latest.json, live_quote, positions, news)
       ├─ fresh → widget shows green "Plan current" strip
       ├─ stale  → SSE plan.stale + badge; agent prompted to refresh
       └─ thesis broken + position open → run_options_research(refresh)
            → new widget with supersedes: old_widget_id
            → SSE thesis.broken with reasons + recommended action

User executes from widget
  → execution_ledger.record(widget_id, legs, prediction, scenarios)
  → periodic job matches position_book → ledger entry
  → monitor runs position-aware rules on watched symbols
```

---

## File Map (new + modified)

| File | Responsibility |
|------|----------------|
| **Create** `integrations/trade_integrations/monitor/__init__.py` | Package entry; exports `MonitorService`, `is_monitor_enabled` |
| **Create** `integrations/trade_integrations/monitor/config.py` | Master enable flag + all thresholds; default **disabled** |
| **Create** `integrations/trade_integrations/monitor/service.py` | Orchestrator: staleness + news + refresh; only entry point for hub/jobs |
| **Create** `integrations/trade_integrations/monitor/plan_staleness.py` | Compare hub vs live market; return `StalenessReport` |
| **Create** `integrations/trade_integrations/monitor/thesis_break.py` | Position + scenario + P&L breach rules |
| **Create** `integrations/trade_integrations/monitor/news_watcher.py` | Deduped headline hash store + material-event classifier |
| **Create** `integrations/trade_integrations/monitor/execution_ledger.py` | Persist widget ↔ execution ↔ thesis metadata |
| **Create** `vibetrading/agent/src/scheduled_research/options_jobs.py` | Scheduled refresh + position monitor jobs |
| **Create** `vibetrading/frontend/src/hooks/useLivePlanContext.ts` | Poll `/trade/plan-context/{ticker}` for drift strip |
| **Modify** `vibetrading/agent/src/trade/hub_bridge.py` | Staleness check after prefetch; auto-emit widget |
| **Modify** `vibetrading/agent/src/api/trade_routes.py` | Ledger write on execute; plan-context endpoint |
| **Modify** `integrations/trade_integrations/dataflows/options_research/widget_payload.py` | `staleness`, `live_context`, `supersedes` fields |
| **Modify** `integrations/trade_integrations/bridge/hub_context.py` | Mandatory widget + staleness hints in prompt |
| **Modify** `stack/vibe/skills/options-advisor/SKILL.md` | Widget required; thesis-break playbook |
| **Modify** `openalgo/mcp/mcpserver.py` | `get_plan_position_status`, staleness in widget tool |
| **Modify** `vibetrading/frontend/src/components/chat/TradePlanWidgetCard.tsx` | Staleness badge, live spot strip, superseded banner |
| **Modify** `.env.example` | New env vars for monitor thresholds |
| **Test** `tests/test_plan_staleness.py` | Staleness + thesis-break unit tests |
| **Test** `tests/test_execution_ledger.py` | Ledger round-trip |
| **Test** `tests/test_widget_auto_emit.py` | hub_bridge auto-widget behavior |

---

## Phase 1 — Plan staleness detection + live context strip

### Task 1: Staleness evaluator module

**Files:**
- Create: `integrations/trade_integrations/monitor/plan_staleness.py`
- Create: `integrations/trade_integrations/monitor/__init__.py`
- Test: `tests/test_plan_staleness.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class StalenessReport:
      ticker: str
      status: Literal["fresh", "stale", "broken"]
      as_of: str
      live_spot: float | None
      plan_spot: float | None
      spot_drift_pct: float | None
      age_minutes: float
      reasons: list[str]
      suggested_action: Literal["none", "refresh", "re_recommend"]
  ```
  ```python
  def evaluate_plan_staleness(
      doc: OptionsResearchDoc,
      *,
      live_spot: float | None = None,
      now: datetime | None = None,
  ) -> StalenessReport
  ```
- Consumes: `OptionsResearchDoc` from hub; optional live spot from OpenAlgo quote helper

**Env thresholds (add to `.env.example`):**
```
OPTIONS_MONITOR_SPOT_DRIFT_PCT=1.5
OPTIONS_MONITOR_MAX_AGE_MINUTES=30
OPTIONS_MONITOR_IV_REGIME_CHANGE=true
```

- [ ] **Step 1: Write failing tests**

```python
# tests/test_plan_staleness.py
from datetime import datetime, timezone, timedelta
from trade_integrations.monitor.plan_staleness import evaluate_plan_staleness

def _doc(spot=100.0, as_of=None, iv_regime="normal"):
    return type("Doc", (), {
        "underlying": "NIFTY",
        "spot": spot,
        "as_of": as_of or datetime.now(timezone.utc).isoformat(),
        "prediction": {"view": "range_bound", "iv_regime": iv_regime},
        "expiry": "2026-07-24",
        "chain_snapshot": {},
        "browse_summary": {},
    })()

def test_spot_drift_marks_stale():
    doc = _doc(spot=100.0)
    report = evaluate_plan_staleness(doc, live_spot=102.0)  # 2% drift
    assert report.status == "stale"
    assert any("spot" in r.lower() for r in report.reasons)

def test_fresh_within_threshold():
    doc = _doc(spot=100.0)
    report = evaluate_plan_staleness(doc, live_spot=100.5)
    assert report.status == "fresh"
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_plan_staleness.py -v`
Expected: `ModuleNotFoundError` or `evaluate_plan_staleness` not defined

- [ ] **Step 3: Implement `evaluate_plan_staleness`**

```python
# integrations/trade_integrations/monitor/plan_staleness.py
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

SPOT_DRIFT_PCT = float(os.getenv("OPTIONS_MONITOR_SPOT_DRIFT_PCT", "1.5"))
MAX_AGE_MIN = float(os.getenv("OPTIONS_MONITOR_MAX_AGE_MINUTES", "30"))

@dataclass
class StalenessReport:
    ticker: str
    status: Literal["fresh", "stale", "broken"]
    as_of: str
    live_spot: float | None
    plan_spot: float | None
    spot_drift_pct: float | None
    age_minutes: float
    reasons: list[str]
    suggested_action: Literal["none", "refresh", "re_recommend"]

def evaluate_plan_staleness(doc, *, live_spot=None, now=None) -> StalenessReport:
    now = now or datetime.now(timezone.utc)
    # parse doc.as_of, compute age_minutes, spot drift vs live_spot
    # return fresh if within thresholds else stale with reasons
    ...
```

- [ ] **Step 4: Run test — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add integrations/trade_integrations/monitor/ tests/test_plan_staleness.py .env.example
git commit -m "feat: add options plan staleness evaluator"
```

---

### Task 2: Live quote fetch helper

**Files:**
- Create: `integrations/trade_integrations/monitor/live_quotes.py`
- Modify: `integrations/trade_integrations/dataflows/openalgo.py` (reuse existing client if present)

**Interfaces:**
- Produces: `fetch_underlying_ltp(ticker: str) -> float | None`

- [ ] **Step 1: Write failing test** — mock OpenAlgo REST; assert LTP parsed for NIFTY
- [ ] **Step 2: Implement thin wrapper** around existing `trade_integrations.dataflows.openalgo` quote call
- [ ] **Step 3: Run tests; commit** `feat: add live underlying LTP helper for plan monitor`

---

### Task 3: Widget payload + UI live context strip

**Files:**
- Modify: `integrations/trade_integrations/dataflows/options_research/widget_payload.py`
- Modify: `vibetrading/frontend/src/components/chat/TradePlanWidgetCard.tsx`
- Modify: `vibetrading/frontend/src/lib/api.ts`

**Interfaces:**
- Produces on widget JSON:
  ```python
  "staleness": {"status": "fresh|stale|broken", "reasons": [...], "spot_drift_pct": 1.2},
  "live_context": {"spot": 24500.0, "fetched_at": "...", "plan_spot": 24200.0}
  ```

- [ ] **Step 1: In `build_options_trade_widget_from_doc`**, call `evaluate_plan_staleness` + `fetch_underlying_ltp`; attach `staleness` and `live_context` blocks
- [ ] **Step 2: Extend `TradePlanWidget` TypeScript type** with optional `staleness` / `live_context`
- [ ] **Step 3: UI** — header strip: green "Plan current · spot X" or amber "Plan may be outdated · spot moved Y% · Refresh"
- [ ] **Step 4: Test** `tests/test_widget_payload.py` — assert staleness block present when drift injected
- [ ] **Step 5: Commit** `feat: show plan staleness and live spot on trade widget`

---

### Task 4: Hub bridge staleness SSE + research context

**Files:**
- Modify: `vibetrading/agent/src/trade/hub_bridge.py`
- Modify: `integrations/trade_integrations/bridge/hub_context.py`

- [ ] **Step 1: After `prefetch_hub_plan`**, run staleness eval; attach `staleness` to panel payload
- [ ] **Step 2: Emit SSE `plan.stale`** when status != fresh:
  ```python
  event_bus.emit(session_id, "plan.stale", {
      "ticker": key,
      "status": report.status,
      "reasons": report.reasons,
      "suggested_action": report.suggested_action,
  })
  ```
- [ ] **Step 3: Extend `format_research_context_for_agent`** with `staleness_status` and instruction to call `get_options_trade_widget(ticker, refresh=true)` when stale
- [ ] **Step 4: `Agent.tsx`** — handle `plan.stale` toast or inline banner in Research panel
- [ ] **Step 5: Commit** `feat: surface plan staleness via SSE and agent context`

---

## Phase 2 — News and event-driven refresh

### Task 5: News watcher with material-event triggers

**Files:**
- Create: `integrations/trade_integrations/monitor/news_watcher.py`
- Modify: `integrations/trade_integrations/dataflows/news_aggregator/aggregator.py` (export headline normalizer)

**Interfaces:**
- Produces:
  ```python
  def check_material_news(ticker: str, since: datetime) -> list[MaterialHeadline]
  def headline_fingerprint(title: str, url: str) -> str
  ```
- Stores seen fingerprints in `reports/hub/_data/news_seen/{ticker}.json`

**Material event keywords (India options):** earnings, results, guidance, downgrade, upgrade, RBI, budget, FII, VIX spike, merger, stake sale, circuit, halt

- [ ] **Step 1: Test** — same headline twice → not material second time; earnings headline → material
- [ ] **Step 2: Implement watcher** using `get_news_aggregated(ticker)` from existing aggregator
- [ ] **Step 3: Commit** `feat: add material news watcher for options refresh triggers`

---

### Task 6: Scheduled options monitor jobs

**Files:**
- Create: `vibetrading/agent/src/scheduled_research/options_jobs.py`
- Modify: `vibetrading/agent/src/scheduled_research/store.py` (register job types if needed)
- Modify: `.env.example`

**Env:**
```
OPTIONS_MONITOR_ENABLE_SCHEDULER=false
OPTIONS_MONITOR_POLL_CRON=*/5 * * * *
OPTIONS_MONITOR_WATCHLIST=NIFTY,BANKNIFTY
```

**Job types:**
- `options_plan_refresh` — full `run_options_research` + `save_options_research` for watchlist tickers with stale/broken status or material news
- `options_position_monitor` — Phase 3; runs thesis-break for ledger entries

- [ ] **Step 1: Mirror `index_jobs.py` pattern** — `run_options_plan_refresh_job`, `dispatch_options_job_sync`
- [ ] **Step 2: On refresh**, write hub + optionally notify active Vibe sessions via shared event bus hook (store last refresh in hub meta)
- [ ] **Step 3: Register on startup** when `OPTIONS_MONITOR_ENABLE_SCHEDULER=true`
- [ ] **Step 4: Commit** `feat: scheduled options plan refresh on staleness and news`

---

## Phase 3 — Execution ledger + position-aware monitoring

### Task 7: Execution ledger

**Files:**
- Create: `integrations/trade_integrations/monitor/execution_ledger.py`
- Modify: `vibetrading/agent/src/api/trade_routes.py`
- Test: `tests/test_execution_ledger.py`

**Storage:** `reports/hub/_data/executions/ledger.json` (or `~/.vibe-trading/executed_plans.json` — prefer hub `_data` for agent access)

**Record shape:**
```python
{
  "execution_id": "ex_NIFTY_abc123",
  "widget_id": "tp_NIFTY_...",
  "underlying": "NIFTY",
  "legs": [...],
  "prediction_view": "range_bound",
  "recommended_name": "Iron Condor",
  "scenarios": [...],
  "executed_at": "ISO",
  "status": "open|closed|partial",
  "broker_order_ids": [...]
}
```

- [ ] **Step 1: Test** record + load + list_open_by_underlying
- [ ] **Step 2: On successful `POST /trade/execute-basket`**, load widget by `widget_id`, append ledger entry
- [ ] **Step 3: MCP tool `get_plan_position_status(widget_id)`** in `mcpserver.py` — returns ledger + matched position rows from `get_position_book`
- [ ] **Step 4: Commit** `feat: link executed baskets to trade plan ledger`

---

### Task 8: Position matching + thesis-break rules

**Files:**
- Create: `integrations/trade_integrations/monitor/thesis_break.py`
- Modify: `integrations/trade_integrations/monitor/plan_staleness.py` (import shared drift helpers)
- Test: extend `tests/test_plan_staleness.py` → `tests/test_thesis_break.py`

**Interfaces:**
- Produces:
  ```python
  def evaluate_thesis_break(
      doc: OptionsResearchDoc,
      ledger_entry: dict,
      *,
      live_spot: float | None,
      position_pnl: float | None,
  ) -> ThesisBreakReport  # broken: bool, reasons: list[str], severity: low|medium|high
  ```

**Rules (configurable via env):**
| Rule | Condition | Severity |
|------|-----------|----------|
| Spot outside expected move | `abs(live_spot - plan_spot) > expected_move_pct` | high |
| Scenario trigger hit | live spot crosses `scenarios[].trigger` for adverse case | high |
| Max loss proximity | live P&L < 80% of `net_max_loss` | medium |
| IV regime flip | `prediction.iv_regime` vs fresh analytics differs | medium |
| Event passed | earnings date < today and view was event-driven | medium |
| Leg mismatch | position symbols don't match ledger legs | high |

- [ ] **Step 1: Write failing tests** for spot-outside-move and max-loss proximity
- [ ] **Step 2: Implement `evaluate_thesis_break`**
- [ ] **Step 3: `options_position_monitor` job** — for each open ledger entry: fetch positions + live spot → if broken, `run_options_research(refresh=True)` + build widget with `supersedes: old_widget_id`
- [ ] **Step 4: Emit SSE `thesis.broken`** to sessions watching that ticker (track via session symbol map in hub_bridge)
- [ ] **Step 5: Commit** `feat: detect thesis break for open option positions`

---

### Task 9: Re-recommendation widget + agent playbook

**Files:**
- Modify: `integrations/trade_integrations/dataflows/options_research/widget_payload.py`
- Modify: `vibetrading/frontend/src/components/chat/TradePlanWidgetCard.tsx`
- Modify: `stack/vibe/skills/options-advisor/SKILL.md`
- Modify: `integrations/trade_integrations/bridge/hub_context.py`

**Widget fields when superseding:**
```python
"supersedes": "tp_NIFTY_oldid",
"revision_reason": "Spot moved 2.4% above expected move; iron condor no longer optimal",
"position_context": {"has_open_position": true, "unrealized_pnl": -1200}
```

- [ ] **Step 1: Add `supersedes` + `revision_reason` to widget builder** (optional kwargs)
- [ ] **Step 2: UI banner** — "Updated recommendation — prior plan may no longer fit your open position"
- [ ] **Step 3: Skill section "Position review"** — when user asks about open trade or `thesis.broken` event: call `get_plan_position_status`, then `get_options_trade_widget(refresh=true)`, explain delta vs old plan
- [ ] **Step 4: Agent context** — inject `[position_context]` block when ledger has open entry for session ticker
- [ ] **Step 5: Commit** `feat: superseding widget and agent playbook for thesis break`

---

## Phase 4 — Widget-by-default enforcement

Today the skill says "preferred" and the agent sometimes answers with markdown only. This phase makes the widget **automatic and mandatory** for options strategy presentation.

### Task 10: Auto-emit widget on options ticker prefetch

**Files:**
- Modify: `vibetrading/agent/src/trade/hub_bridge.py`
- Modify: `vibetrading/agent/src/api/sessions_routes.py` (if prefetch path differs)
- Test: `tests/test_widget_auto_emit.py`

**Behavior:** When `prefetch_hub_plan` succeeds for `asset_type=options` and plan_status is `complete` or `partial`, also call `build_options_trade_widget(ticker, refresh=False)` and emit `trade_plan.widget` SSE **before** the agent's first token — unless a widget for that ticker was already emitted in the last 10 minutes in this session.

- [ ] **Step 1: Test** — mock prefetch → assert `trade_plan.widget` frame emitted once per session per ticker
- [ ] **Step 2: Implement in `hub_bridge`** after research artifact emit:
  ```python
  from trade_integrations.dataflows.options_research.widget_payload import build_options_trade_widget
  widget = build_options_trade_widget(key, refresh=False)
  _emit(event_bus, session_id, "trade_plan.widget", widget)
  ```
- [ ] **Step 3: Session dedup** — `session_widget_emitted: dict[str, set[str]]` keyed by session_id
- [ ] **Step 4: Commit** `feat: auto-emit options trade widget on hub prefetch`

---

### Task 11: Mandatory widget contract in agent prompt + skill

**Files:**
- Modify: `integrations/trade_integrations/bridge/hub_context.py`
- Modify: `stack/vibe/skills/options-advisor/SKILL.md`
- Modify: `openalgo/mcp/mcpserver.py` — tool descriptions

**Prompt addition (hub_context footer):**
```
MANDATORY: When presenting an options strategy recommendation, ranked strategies,
scenarios, or trade plan to the user, you MUST call get_options_trade_widget(ticker)
in the same turn. Do not answer with markdown tables or prose-only strategy lists.
The chat UI requires the widget for payoff, charges, and execution. If a widget is
already visible for this ticker, you may reference it but still call the tool with
refresh=true if plan_status is stale or the user asks for updates.
```

**Skill changes:**
- Rename Step 2b from "preferred" to **"required"**
- Add gate: "If you are about to name a strategy or legs without calling `get_options_trade_widget`, stop and call the tool first."

- [ ] **Step 1: Update `format_research_context_for_agent`** with MANDATORY block
- [ ] **Step 2: Update options-advisor SKILL.md** — required language, examples of wrong (markdown-only) vs right (tool call)
- [ ] **Step 3: MCP docstrings** — `get_options_trade_widget`: "Required for any strategy recommendation shown to the user."
- [ ] **Step 4: Commit** `feat: require trade widget for options strategy answers`

---

### Task 12: Response guard (optional safety net)

**Files:**
- Create: `vibetrading/agent/src/trade/widget_guard.py`
- Modify: `vibetrading/agent/src/api/sessions_routes.py` or agent completion hook

**Behavior:** Post-turn heuristic — if user message was options-related, agent response mentions strategy keywords (iron condor, straddle, CE, PE, strike) and **no** widget tool was called this turn, automatically invoke `build_options_trade_widget` and append SSE widget event.

- [ ] **Step 1: Implement `needs_widget_guard(user_msg, agent_text, tools_called) -> bool`**
- [ ] **Step 2: Wire into agent completion callback** (low priority — skip if invasive)
- [ ] **Step 3: Test** markdown-only mock response triggers widget emit
- [ ] **Step 4: Commit** `feat: auto-inject widget when agent omits tool on strategy answer`

---

### Task 13: Plan context API for frontend polling

**Files:**
- Modify: `vibetrading/agent/src/api/trade_routes.py`
- Create: `vibetrading/frontend/src/hooks/useLivePlanContext.ts`
- Modify: `vibetrading/frontend/src/components/chat/TradePlanWidgetCard.tsx`

**Endpoint:** `GET /trade/plan-context/{ticker}` → `{ staleness, live_context, material_news_count, open_position: bool }`

- [ ] **Step 1: Implement endpoint** (staleness + news watcher + ledger lookup)
- [ ] **Step 2: Hook polls every 60s** while widget card mounted
- [ ] **Step 3: Update widget strip** without full page refresh
- [ ] **Step 4: Commit** `feat: poll live plan context for trade widget cards`

---

## Phase 5 — Options prediction ledger (calibration, optional)

Mirror `index_research/prediction_ledger.py` for options recommended strategies.

**Files:**
- Create: `integrations/trade_integrations/dataflows/options_research/prediction_ledger.py`
- Modify: `integrations/trade_integrations/dataflows/options_research/aggregator.py` — log on save

- [ ] **Step 1: Log each `recommended`** with view, legs, spot, expiry at `as_of`
- [ ] **Step 2: `reconcile_options_predictions()`** at expiry — actual spot vs expected move
- [ ] **Step 3: Feed calibration stats into `strategy_ranker.py` confidence** (future; stub reconcile only)
- [ ] **Step 4: Commit** `feat: options prediction ledger for self-calibration`

---

## Environment Variables Summary

Add to `.env.example`:

```bash
# Plan monitor
#OPTIONS_MONITOR_ENABLE_SCHEDULER=false
#OPTIONS_MONITOR_POLL_CRON=*/5 * * * *
#OPTIONS_MONITOR_WATCHLIST=NIFTY,BANKNIFTY
#OPTIONS_MONITOR_SPOT_DRIFT_PCT=1.5
#OPTIONS_MONITOR_MAX_AGE_MINUTES=30
#OPTIONS_MONITOR_IV_REGIME_CHANGE=true
#OPTIONS_MONITOR_MAX_LOSS_PCT_THRESHOLD=80
#OPTIONS_MONITOR_AUTO_WIDGET_ON_PREFETCH=true
```

---

## Testing Checklist (manual)

- [ ] Mention NIFTY in Vibe → Research panel + **widget card appear without agent tool call**
- [ ] Wait or simulate spot drift → amber staleness strip; agent context says refresh
- [ ] Inject material news headline → scheduled job refreshes hub; new `as_of` in widget
- [ ] Execute widget basket → ledger entry created; `get_plan_position_status` returns match
- [ ] Simulate spot move past expected move with open position → `thesis.broken` SSE + superseding widget
- [ ] Ask agent "what should I trade?" → must call `get_options_trade_widget`; no markdown-only strategy list
- [ ] Stale plan + user follow-up → agent uses `refresh=true`; updated legs and charges in widget

---

## Implementation Order (recommended)

| Order | Phase | Rationale |
|-------|-------|-----------|
| 1 | Phase 4 Tasks 10–11 | Immediate UX win — widget always visible; low risk |
| 2 | Phase 1 | Staleness visibility without background jobs |
| 3 | Phase 3 Tasks 7–9 | Position-aware re-recommendation (core user ask) |
| 4 | Phase 2 | News-driven refresh completes "live events" requirement |
| 5 | Phase 4 Task 12–13 | Polish — guard + polling |
| 6 | Phase 5 | Calibration — nice-to-have |

---

## Out of Scope (explicit)

- WebSocket streaming of full option chain into Vibe (use OpenAlgo poll + existing Strategy Builder WS for leg P&L only)
- Paid news APIs (RavenPack, Bloomberg)
- Auto-execution on re-recommendation
- Cursor canvas artifacts (widgets stay in-chat per product north star)
- Stock position monitor (follow-up plan; stock widget parity exists but thesis-break is options-first)

---

## Self-Review (spec coverage)

| Requirement | Covered by |
|-------------|------------|
| Real-time reactions to live news/data | Phase 1–2: staleness, news watcher, scheduled refresh |
| Predictions/plans based on latest events | Phase 2 Task 6 full re-research; widget `refresh=true` path |
| Re-recommend when old plan fails with positions | Phase 3: ledger, thesis-break, superseding widget |
| Agent generates widget by default for options | Phase 4: auto-emit, mandatory prompt/skill, optional guard |

No TBD placeholders remain in task steps above. Task 12 (response guard) is explicitly optional.
