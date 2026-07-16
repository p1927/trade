# Autonomous Trading Agents — Design Spec

**Date:** 2026-07-16  
**Status:** Draft — pending user review  
**Goal:** Persistent autonomous trading agents, each with its own live chat window — watch thinking in real time, guide when needed, create new agents conversationally from the Autonomous section.

---

## Summary

The **`/autonomous` section** is the home for all autonomous trading. It is **not** a read-only dashboard — it is a **multi-chat workspace** where every autonomous agent is its own agent chat (same UX as `/agent`: thinking timeline, tool calls, reasoning, widgets, proposal cards).

- **Orchestrator** — a card (or dedicated "Create agent" entry) on the hub; open it to describe agents via chat; MCP propose → confirm → a new **agent card** appears on the hub.
- **Agent cards** — each created agent is a **card on the hub** (not a tab, not a global sidebar entry). Cards show status, symbols, confidence, last activity. **Click a card** → opens that agent's full session chat (thinking stream, tools, input). **Back** → returns to the card hub.

Creation, monitoring, and guidance all happen inside `/autonomous`. The global app sidebar only has one nav link ("Autonomous"). `/agent` stays for ad-hoc interactive research.

**Maximize reuse:** existing Vibe session runtime, SSE stream, `Agent.tsx` chat components, `scheduled_research` executor, `trade_integrations/auto_paper` (market feedback, thesis-break, lifecycle), OpenAlgo MCP tools, hub research artifacts, options-advisor skills.

---

## Decisions (locked)

| Decision | Choice |
|----------|--------|
| Convergence model | **Iterative thesis refinement** — re-reason each full turn; act when confidence ≥ threshold |
| Agent scope | **Generic per-instance** — custom symbols, mandate, constraints; bounded product actions |
| Watch model | **Dual cadence + alerts** — lightweight watch, alert → immediate full reasoning, scheduled deep research |
| Primary UX | **Two-view `/autonomous` page** — card hub (default) ↔ full session chat (drill-down) |
| Agent navigation | **Agent cards on hub** — click card opens session; Back returns to hub |
| Creation UX | **Orchestrator chat → MCP propose → confirmation card → commit** |
| User guidance | **Same session** — user messages interleave with scheduler turns; agent sees guidance on next turn |
| Architecture | **`autonomous_agents` orchestration layer** over existing session + scheduler + trade_integrations |
| v1 execution | **Paper only** (India market hours) via OpenAlgo sandbox |
| Default watch | **5–10 min** (news, events, spot/OI drift) — user can override verbally |
| Default research | **60–120 min** full reasoning — user can override verbally |

---

## UX layout

One nav entry in the global sidebar: **Autonomous** → `/autonomous`. The page has **two views** with drill-down navigation.

### View A — Card hub (default)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Autonomous                                                             │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────┐ │
│  │ + Create agent      │  │ ● NIFTY event vol   │  │ ○ RELIANCE swing│ │
│  │   (orchestrator)    │  │   NIFTY · running   │  │   RELIANCE · paused│
│  │                     │  │   conf 78% · 2m ago │  │   conf 62%      │ │
│  │   Click → open      │  │   [expand ▾]        │  │   [expand ▾]    │ │
│  │   orchestrator chat │  │   Click → session   │  │   Click → session│ │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

**Agent card (collapsed):** name, primary symbol(s), status dot, confidence, last tick relative time, streaming spinner if turn in flight.

**Agent card (expanded on hub):** optional inline peek — latest thesis one-liner, last decision, next watch/research countdown. Still on hub; does not replace session chat.

**Create agent card:** opens orchestrator session (View B with `?view=orchestrator`).

### View B — Session chat (drill-down)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ← Back to agents          NIFTY event vol · running · conf 78%         │
├─────────────────────────────────────────────────────────────────────────┤
│  CHAT PANE (AgentChat — copied from Agent.tsx)                          │
│  · ThinkingTimeline / tool calls / reasoning (live SSE)                 │
│  · AutonomousAgentProposalCard (orchestrator session only)              │
│  · TradePlanWidgetCard, status chips, etc.                              │
│  · Input: talk to orchestrator OR guide the selected agent              │
└─────────────────────────────────────────────────────────────────────────┘
```

**Navigation:**
- Hub card **click** (body/title) → View B for that agent's `vibe_session_id`
- **← Back** → View A card hub (SSE disconnects or stays background-subscribed per agent — TBD in impl; default: disconnect on back, reconnect on re-open)
- After agent creation confirm → new card on hub; optionally auto drill-down into that agent's session

**Not in scope:** per-agent entries in `Layout.tsx` global session list. Agents appear only as cards on `/autonomous` hub.

---

## User flows

### Flow 1 — Create agent (orchestrator session)

```
User opens /autonomous → card hub (View A)
  → Clicks "Create agent" card → View B orchestrator chat
  → "Create an agent to watch NIFTY and paper trade on RBI events"
  → Orchestrator agent asks only for missing details (or uses defaults)
  → propose_autonomous_agent(symbols=["NIFTY"], watch_interval_min=7, …)
  → AutonomousAgentProposalCard renders in chat
  → User clicks Confirm → POST /autonomous-agents/commit
  → Backend: create aa_<id>, vibe_session_id, register schedules, start
  → User taps ← Back (or auto-navigate) → new agent card visible on hub
  → Optional: auto drill-down into new agent's session chat
```

**Defaults when user doesn't specify:**

| Parameter | Default |
|-----------|---------|
| `watch_interval_min` | 7 (range 5–10 acceptable) |
| `research_interval_min` | 90 |
| `budget_inr` | 20,000 |
| `max_daily_loss_inr` | 2,000 |
| `confidence_threshold` | 75 |
| `mode` | paper |
| `mandate` | inferred from user description |

### Flow 2 — Watch agent work (per-agent session)

```
User on card hub → clicks "NIFTY event vol" card → View B session chat
  → URL: /autonomous?agent=aa_abc…
  → Loads bound vibe_session_id; connects SSE (same as Agent.tsx)
  → Scheduler watch/research ticks stream live in chat
  → User taps ← Back → returns to card hub (View A)
```

### Flow 3 — Guide agent (user interjection)

```
While agent is running, user types: "Don't enter until VIX > 14"
  → Message sent to same vibe_session_id via existing send_message API
  → Stored in session; injected into next turn prompt as USER_GUIDANCE block
  → If agent is mid-turn, guidance applies to next turn (no hard interrupt in v1)
  → Agent acknowledges and adjusts thesis on next watch/research tick
```

### Flow 4 — Scheduler tick loop (backend)

```
Watch tick (every 5–10 min default)
  → build_market_feedback (auto_paper), news/event checks, thesis-break
  → Append watch summary to session as system turn marker (visible in chat)
  → If material alert → dispatch FULL_REASONING immediately

Research tick (every 60–120 min default)
  → FULL_REASONING via send_message on bound session

FULL_REASONING turn
  → Turn prompt: hub research + market feedback + prior thesis + user guidance
  → Agent runs bounded OpenAlgo MCP + research tools
  → Outputs confidence; acts if ≥ threshold (widget, paper basket, record decision)
  → All streamed to chat via existing SSE
```

---

## Code reuse strategy (frontend)

**Do not rewrite chat from scratch.** Extract shared pieces from `Agent.tsx`:

| Extract | Used by |
|---------|---------|
| `AgentChatPane` | `/agent`, `/autonomous?agent=…`, orchestrator |
| `useAgentSSE(sessionId)` | all chats (already `useSSE` + `useAgentStore`) |
| `ThinkingTimeline`, `MessageBubble`, `ToolProgressIndicator` | all chats |
| `AutonomousAgentProposalCard` | orchestrator + per-agent (during (re)configuration) |
| `TradePlanWidgetCard`, `ContextDrawer` | per-agent chat |

**New files (thin wrappers):**

- `pages/Autonomous.tsx` — routes between hub (View A) and session (View B)
- `components/autonomous/AutonomousAgentCard.tsx` — hub card (collapsed + expandable summary)
- `components/autonomous/AutonomousAgentHub.tsx` — grid of cards + create-agent card
- `components/autonomous/AutonomousSessionHeader.tsx` — ← Back, agent name, status strip
- `components/autonomous/OrchestratorWelcome.tsx` — create-agent chat welcome

**Router / view state:**

```
/autonomous                      → View A (card hub)
/autonomous?agent=orchestrator   → View B (orchestrator create session)
/autonomous?agent=aa_<id>        → View B (that agent's session chat)
```

Card click sets `?agent=` and mounts `AgentChatPane`. Back clears `?agent=` (or sets hub-only) → View A.

---

## Session model (1:1 agent ↔ chat)

Every autonomous agent instance binds **exactly one** Vibe session:

```json
{
  "session.config": {
    "autonomous_agent_id": "aa_abc…",
    "session_kind": "autonomous_agent",
    "symbols": ["NIFTY"],
    "orchestrator": false
  }
}
```

Orchestrator chat uses `session_kind: "autonomous_orchestrator"` (no `autonomous_agent_id`).

**On commit:**
1. `svc.create_session(title="autonomous:NIFTY event vol", config={…})`
2. Store `vibe_session_id` on `aa_<id>` instance
3. Scheduler turns call `svc.send_message(vibe_session_id, turn_prompt)` — same path as auto-paper scheduled jobs today

User messages and scheduler turns share one transcript — full audit trail, user can scroll back.

---

## Agent instance model

```json
{
  "id": "aa_8f3c2a1b9e004d5f6a7b8c9d0e1f2a3b",
  "type": "autonomous_agent.instance",
  "name": "NIFTY event vol watcher",
  "status": "running",
  "vibe_session_id": "8efc6ad241b5",
  "symbols": ["NIFTY", "BANKNIFTY"],
  "mandate": "Paper trade NIFTY options on event volatility; flatten by close.",
  "constraints": {
    "mode": "paper",
    "budget_inr": 20000,
    "max_daily_loss_inr": 2000,
    "confidence_threshold": 75,
    "market_hours_only": true
  },
  "schedules": {
    "watch_ms": 420000,
    "research_ms": 5400000
  },
  "alert_rules": {
    "spot_move_pct": 0.5,
    "thesis_break": true,
    "news_enabled": true
  },
  "thesis": {
    "direction": "event_volatility",
    "strategy": "long_straddle",
    "confidence": 78,
    "rationale": "...",
    "updated_at": "2026-07-16T12:00:00Z"
  },
  "user_guidance": [],
  "last_watch_at": null,
  "last_full_reasoning_at": null,
  "streaming": false,
  "proposal_id": "aap_<uuid>"
}
```

**Storage:** `reports/hub/_data/autonomous_agents/{id}.json`

---

## Reuse map (backend / integrations)

| Existing module | Role in autonomous agents |
|-----------------|---------------------------|
| `vibetrading/agent` session service | 1:1 chat per agent; SSE streaming; user send_message |
| `scheduled_research/executor.py` | Watch + research cron jobs per agent |
| `trade_integrations/auto_paper/market_feedback.py` | Watch tick: news, spot, OI, alerts |
| `trade_integrations/monitor/thesis_break.py` | Alert → full reasoning |
| `trade_integrations/auto_paper/agent_mandate.py` | Turn prompt templates (adapt for per-agent) |
| `trade_integrations/auto_paper/lifecycle.py` | Position state in prompts |
| `trade_integrations/auto_paper/mcp_actions.py` | Paper execution patterns |
| `openalgo/mcp/mcpserver.py` | propose, status, market feedback, execute basket, record decision |
| Hub `options_research` / `company_research` | Research context in full turns |
| `stack/vibe/skills/options-advisor` | Agent skill for bounded autonomous behavior |
| `live/runtime/triggers.py` | Market-hours gating for India session |

**New thin layer:** `integrations/trade_integrations/autonomous_agents/`
- `store.py` — instance CRUD
- `proposals.py` — propose/commit consent
- `watch.py` — lightweight watch dispatcher
- `turns.py` — full reasoning prompt builder + dispatch
- `jobs.py` — register/unregister scheduled_research jobs

---

## MCP tools (OpenAlgo + Vibe mirror)

| Tool | Caller | Purpose |
|------|--------|---------|
| `propose_autonomous_agent` | Orchestrator agent (read-only) | Draft proposal; return `missing_fields` or ready |
| `get_autonomous_agent_status` | Per-agent turns | Session state at turn start |
| `get_auto_paper_market_feedback` | Watch + full turns | **Reuse as-is** — news, alerts, positions |
| `get_options_trade_widget` / `get_options_trade_plan` | Full turns | **Reuse as-is** |
| `execute_auto_paper_basket` | Full turns (paper act) | **Reuse as-is** |
| `record_autonomous_decision` | Full turns | Log ENTER/EXIT/HOLD/SKIP (wrap `record_auto_paper_decision` pattern) |
| *(no commit tool)* | — | User confirms via UI only |

### `propose_autonomous_agent` defaults

```python
watch_interval_min: int = 7      # 5–10 min band
research_interval_min: int = 90    # 60–120 min band
budget_inr: float = 20000
max_daily_loss_inr: float = 2000
confidence_threshold: int = 75
mode: str = "paper"
```

All overridable by user in natural language; agent passes explicit values when user states them.

---

## Confirmation card

`AutonomousAgentProposalCard` — same consent pattern as `MandateProposalCard`:
- **Confirm** → `POST /autonomous-agents/commit` (creates session + instance + schedules)
- **Adjust** → chat message → re-propose
- Post-commit → new card on hub + optional auto drill-down to agent session

---

## Backend API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/autonomous-agents` | List for hub cards (status, symbols, streaming, confidence, thesis peek) |
| `GET` | `/autonomous-agents/{id}` | Instance detail |
| `POST` | `/autonomous-agents/commit` | User-confirmed create |
| `POST` | `/autonomous-agents/{id}/pause` | Pause schedules |
| `POST` | `/autonomous-agents/{id}/resume` | Resume |
| `POST` | `/autonomous-agents/{id}/stop` | Stop + unregister jobs |
| `POST` | `/autonomous-agents/orchestrator/session` | Get-or-create orchestrator vibe session |

Existing session APIs (`GET /sessions`, `POST /sessions/{id}/messages`, SSE) serve per-agent chats — no duplicate chat API.

---

## Scheduler jobs (per agent)

| Job ID | `job_type` | Default schedule |
|--------|------------|------------------|
| `aa_{id}-watch` | `autonomous_agent_watch` | `420000` ms (7 min) |
| `aa_{id}-research` | `autonomous_agent_research` | `5400000` ms (90 min) |

Alert from watch → immediate `send_message` full-reasoning (dedupe if turn in flight).

**Env:** `AUTONOMOUS_AGENTS_ENABLE_SCHEDULER=1`

---

## SSE events

| Event | Purpose |
|-------|---------|
| `autonomous_agent.proposal` | Confirmation card in orchestrator chat |
| `autonomous_agent.committed` | Hub card refresh + optional drill-down to new agent session |
| `autonomous_agent.watch` | Lightweight watch summary in chat timeline |
| `autonomous_agent.status` | Status bar update (confidence, next fire) |

Relay in `sessions_routes.py` alongside `mandate.proposal`.

---

## Relationship to `/agent`

| Surface | Purpose |
|---------|---------|
| `/agent` | Ad-hoc interactive research, one-off questions, manual trading help |
| `/autonomous` | Always-on agents — each with persistent chat, scheduler, bounded autonomy |

Same underlying session runtime. Autonomous sessions tagged in `config.session_kind`; surfaced only as cards on `/autonomous` hub, not in global Layout session list.

---

## Implementation phases

| Phase | Deliverable |
|-------|-------------|
| **1** | `autonomous_agents` store + instance model + commit API + orchestrator session |
| **2** | `propose_autonomous_agent` MCP + Vibe tool + proposal persistence |
| **3** | Watch + research dispatchers → `send_message` on bound session |
| **4** | Extract `AgentChatPane`; build `Autonomous.tsx` hub + session views + agent cards |
| **5** | `AutonomousAgentProposalCard` + SSE relay |
| **6** | Turn prompts (reuse auto_paper mandate patterns) + user guidance injection + skills |

---

## Defaults & limits (confirmed)

- **Watch:** 7 min default (5–10 min acceptable; user can say "every 5 minutes")
- **Research:** 90 min default (user can say "every 2 hours")
- **Proposal TTL:** 30 min
- **Max concurrent agents:** 10 (soft cap)

---

## Self-review checklist

- [x] `/autonomous` hub = **agent cards** (not tabs, not global sidebar entries)
- [x] Card expand on hub for summary peek; click card → full session chat
- [x] ← Back returns from session chat to card hub
- [x] Orchestrator via "Create agent" card; new agent → new card on hub
- [x] Full SSE stream in session view; user can talk to agent there
- [x] Reuses session runtime, SSE, trade_integrations, OpenAlgo MCP
- [x] Defaults: watch 5–10 min, research 60–120 min; overridable verbally
- [x] No manual form
- [x] Consent propose/commit pattern preserved
