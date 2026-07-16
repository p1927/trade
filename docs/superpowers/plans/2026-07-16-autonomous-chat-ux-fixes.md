# Autonomous Agent Chat UX & Loop Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make autonomous agent sessions behave as users expect: one clear bootstrap outcome (trade plan or explicit HOLD), quiet watch ticks, persisted thesis/confidence on cards, and no implementation-debug prose in chat.

**Architecture:** Fix the loop at three layers — (1) backend state: persist thesis on `record_autonomous_decision`, fix streaming dedupe, dedupe watch paths; (2) scheduler: defer research until bootstrap turn completes, suppress noisy system messages; (3) prompts/UI: enforce structured user-facing output format, surface confidence from `last_decision`, collapse watch lines into card status.

**Tech Stack:** Python (`trade_integrations/autonomous_agents`, `nautilus_openalgo_bridge`), Vibe agent (`vibetrading/agent`), React (`AutonomousAgentCard`, `Agent.tsx`)

## Global Constraints

- Vibe = strategy mind; OpenAlgo = sole execution authority; Nautilus = watch engine
- India agents: no `get_auto_paper_market_feedback` for watch; bridge owns alerts
- Paper first; structured JSON on agent instance (`thesis`, `watch_spec`, `last_decision`)
- Open-source / free APIs only

---

## Investigation summary — issues in the reported chat

### A. User-facing chat problems (what looks wrong in the transcript)

| # | Symptom in chat | Root cause |
|---|-----------------|------------|
| **U1** | "10-handoff cycle", "verification reads may also be lost", "commit from cached context" | Agent leaking **dev/E2E heuristics** (likely persistent memory recall or prior integration-test session context) — not trading rationale |
| **U2** | "synthetic alert (19:45Z)" | Test-harness vocabulary from bridge E2E plans leaking into production chat |
| **U3** | Audit table (`pa_6b4744ef…`, `kind=decision_recorded`) | No **user-facing response template**; agent mirrors internal audit IDs |
| **U4** | "Per-turn status" / "Next-turn expectation" sections | Prompt asks for autonomous decision but not a **fixed trader-facing format**; model improvises ops commentary |
| **U5** | Spot/VIX labeled "(cached)" with no refresh narrative | Status tool returns cached quotes; agent cites without calling live browse |
| **U6** | Scorer shows iron_condor EV ₹2,623 / score 0.53 but HOLD @ 40% | No requirement to **reconcile scorer vs confidence** in rationale; thesis not persisted so reasoning restarts each turn |
| **U7** | No trade widget, payoff, or step-by-step plan in chat | Below-threshold path skips `get_options_trade_widget`; UI doesn't render a **decision summary card** from `last_decision` |
| **U8** | Repeated identical HOLD reasoning | Research job fires at T+60s while bootstrap in flight; empty `thesis` block on every prompt |

### B. Infrastructure / loop problems (what generates the noise)

| # | Symptom | Root cause (verified in code) |
|---|---------|-------------------------------|
| **I1** | `[autonomous_watch] market closed — summary only` every ~7 min | `run_watch_tick` always appends system message when `market_hours_only` and session closed (`watch.py:76-82`) |
| **I2** | `[autonomous_watch] NIFTY — no material alerts` every ~5–7 min | Scheduled `{agent_id}-watch` + bootstrap watch both call `run_watch_tick`; each appends to session chat |
| **I3** | Duplicate lines at same timestamp (22:16 ×2) | Overlapping paths: scheduler watch **and** detached Nautilus `poll_loop` both poll; API restart re-registers jobs with `watch_next_run=now` (`scheduled_routes.py:131-145`, `autonomous_agent_jobs.py:60`) |
| **I4** | Card shows no `conf` despite chat saying 40% | `record_autonomous_decision` has **no `confidence` param**; `agent.thesis` only updated on position handoff (`mcp_actions.py`, `handoff.py`) |
| **I5** | `streaming` guard ineffective for bridge alerts | `vibe_trigger.dispatch_watch_alert` clears `streaming` in `finally` immediately after HTTP POST, before turn completes (`vibe_trigger.py:177-180`) — session service clears it correctly in `_finalize_autonomous_agent_turn` |
| **I6** | Bootstrap + research overlap | `register_agent_jobs` sets research `next_run_at=now+60s` regardless of bootstrap completion (`autonomous_agent_jobs.py:78`) |
| **I7** | Watch summaries invisible to agent | System messages stored in chat but **excluded from LLM history** (`session/service.py:559`) |
| **I8** | `bootstrap_status=done` before turn finishes | `bootstrap_agent` marks done after dispatch, not after `record_autonomous_decision` (`bootstrap.py:38-42`) |

### Expected vs actual user journey

```
EXPECTED:  Confirm card → bootstrap research → clear recommendation (ENTER or HOLD + why)
           → quiet watch → alert only when material → revise → execute/exit

ACTUAL:    Confirm → market-closed watch line → huge meta-audit prose → HOLD
           → watch spam every 5–7 min → card missing confidence → repeat analysis
```

---

### Task 1: Persist thesis + confidence on every decision

**Files:**
- Modify: `integrations/trade_integrations/autonomous_agents/mcp_actions.py`
- Modify: `openalgo/mcp/mcpserver.py` (`record_autonomous_decision` signature)
- Modify: `integrations/trade_integrations/auto_paper/mcp_actions.py` (`record_decision` if needed)
- Test: `tests/test_autonomous_mcp_actions.py` (new)

**Interfaces:**
- Consumes: existing `get_agent` / `save_agent`
- Produces: `mcp_record_decision(..., confidence: int | None = None, direction: str | None = None, strategy: str | None = None)` writes `agent.thesis` with `{direction, strategy, confidence, rationale, updated_at, decision}`

- [ ] **Step 1: Write failing test**

```python
def test_record_decision_persists_thesis(tmp_path, monkeypatch):
    # setup agent fixture
    result = mcp_record_decision(
        agent_id="aa_test",
        decision="HOLD",
        rationale="Low IV, range-bound",
        confidence=40,
        direction="neutral",
        strategy="iron_condor",
    )
    agent = get_agent("aa_test")
    assert agent["thesis"]["confidence"] == 40
    assert agent["thesis"]["strategy"] == "iron_condor"
    assert agent["last_decision"]["decision"] == "HOLD"
```

- [ ] **Step 2: Run test** — expect FAIL (confidence not stored)

- [ ] **Step 3: Implement** — merge into `agent.thesis` on every `mcp_record_decision`; add optional MCP params

- [ ] **Step 4: Run test** — expect PASS

- [ ] **Step 5: Commit** — `feat(autonomous): persist thesis confidence on record_decision`

---

### Task 2: Fix streaming lifecycle for bridge alert dedupe

**Files:**
- Modify: `integrations/nautilus_openalgo_bridge/vibe_trigger.py:177-180`
- Test: `tests/test_nautilus_vibe_trigger.py`

**Interfaces:**
- Consumes: `session.service._finalize_autonomous_agent_turn` (already clears streaming)
- Produces: `dispatch_watch_alert` no longer clears `streaming` in `finally`; only clears on dispatch **error** before turn starts

- [ ] **Step 1: Write failing test** — mock HTTP success; assert `streaming` stays True until external clear

- [ ] **Step 2: Run test** — expect FAIL

- [ ] **Step 3: Remove `finally` streaming clear; on POST failure reset streaming like `dispatch_full_reasoning` does

- [ ] **Step 4: Run test** — expect PASS

- [ ] **Step 5: Commit** — `fix(nautilus-bridge): keep streaming until vibe turn completes`

---

### Task 3: Single-owner watch — stop chat spam

**Files:**
- Modify: `integrations/trade_integrations/autonomous_agents/watch.py`
- Modify: `vibetrading/agent/src/scheduled_research/autonomous_agent_jobs.py`
- Modify: `integrations/trade_integrations/autonomous_agents/runtime_status.py`

**Interfaces:**
- Consumes: `nautilus_watch.get_watch_process_status`, `agent.last_watch_at`
- Produces: `run_watch_tick` behavior matrix:

| Condition | Chat message | Agent JSON |
|-----------|--------------|------------|
| Market closed + `market_hours_only` | **None** (update `last_watch_at` only) | `watch_summary: {status: closed}` |
| Nautilus detached process alive | **None** on no-alert ticks | `last_watch_summary` field |
| Material alert | One user turn via `dispatch_full_reasoning` | alert payload |
| Legacy / no Nautilus | Keep current system summary | unchanged |

- [ ] **Step 1: Write failing test** — market closed tick must not call `send_message`

- [ ] **Step 2: Implement `_append_watch_system_message` guard** + `should_post_watch_to_chat(agent, feedback)`

- [ ] **Step 3: When detached Nautilus alive, scheduler watch sets `last_watch_summary` only** (no `run_once(trigger_vibe=True)` duplicate)

- [ ] **Step 4: Expose `last_watch_summary` on runtime API for card chip** ("no alerts" / "spot +0.6%")

- [ ] **Step 5: Commit** — `fix(autonomous): suppress redundant watch chat lines`

---

### Task 4: Scheduler sequencing — bootstrap before research

**Files:**
- Modify: `vibetrading/agent/src/scheduled_research/autonomous_agent_jobs.py`
- Modify: `integrations/trade_integrations/autonomous_agents/bootstrap.py`
- Modify: `vibetrading/agent/src/session/service.py` (`_finalize_autonomous_agent_turn`)

**Interfaces:**
- Consumes: `agent.bootstrap_status`, `agent.streaming`, `agent.last_decision`
- Produces: research job `next_run_at` = max(now + research_ms, bootstrap_completed_at + 30s); bootstrap `done` only after first `record_autonomous_decision` or turn finalization timeout

- [ ] **Step 1: Write failing test** — research job not due while `bootstrap_status in (pending, running)`

- [ ] **Step 2: Defer research registration** until bootstrap marks done

- [ ] **Step 3: In `_finalize_autonomous_agent_turn`, if bootstrap was `running` and `last_decision` set → mark bootstrap `done`**

- [ ] **Step 4: `dispatch_full_reasoning` skip if identical research turn within cooldown** (e.g. 15 min, no alert, same decision)

- [ ] **Step 5: Commit** — `fix(autonomous): sequence bootstrap before scheduled research`

---

### Task 5: User-facing output contract (prompt + UI)

**Files:**
- Modify: `integrations/trade_integrations/autonomous_agents/turns.py` (`_RUNNING_AGENT_FOOTER`, bootstrap block)
- Modify: `integrations/trade_integrations/execution/prompt_fragments.py`
- Modify: `vibetrading/frontend/src/components/autonomous/AutonomousAgentCard.tsx`
- Modify: `vibetrading/frontend/src/pages/Autonomous.tsx` (session header)

**Interfaces:**
- Produces: mandatory assistant structure:

```markdown
## Decision: HOLD (confidence 40% — below 60% gate)
**View:** neutral range · NIFTY 24,072 · VIX 12.9 (low IV)
**Strategy considered:** iron_condor (scorer EV ₹2,623) — deferred because [reason]
**Watch:** spot_move ≥0.5%, thesis_break, news — no material alerts since [time]
**Next action:** ENTER if confidence ≥60% after [specific trigger]
```

Forbidden phrases list in prompt: "handoff cycle", "cached context", "synthetic alert", "audit pa_", "Next-turn expectation"

- [ ] **Step 1: Update `_RUNNING_AGENT_FOOTER`** with template + forbidden internal jargon

- [ ] **Step 2: Bootstrap checklist step 3** — must call `record_autonomous_decision` with `confidence`, `direction`, `strategy` args

- [ ] **Step 3: Card reads `thesis.confidence` OR `last_decision.confidence`** fallback

- [ ] **Step 4: Filter autonomous session rendering** — collapse consecutive identical `[autonomous_watch]` system rows in `Agent.tsx` (group under "Watch log")

- [ ] **Step 5: Commit** — `feat(autonomous): trader-facing decision template and card confidence`

---

### Task 6: Memory / E2E context isolation

**Files:**
- Modify: `vibetrading/agent/src/agent/context.py` (auto-recall filter)
- Modify: `vibetrading/agent/src/trade/session_context.py` (`memory_matches_session`)

**Interfaces:**
- Consumes: `session.config.session_kind == "autonomous_agent"`
- Produces: exclude memories tagged `e2e`, `integration_test`, `handoff`, `idempotent` from autonomous agent sessions

- [ ] **Step 1: Write test** — autonomous session recall skips E2E memories

- [ ] **Step 2: Tag existing dev memories or filter by title/body keywords**

- [ ] **Step 3: Set `e2e_integration_test` flag on harness agents only** (already exists; verify production agents don't inherit)

- [ ] **Step 4: Commit** — `fix(autonomous): block E2E memories from production agent turns`

---

### Task 7: Tool-call enforcement for bootstrap / research turns

**Files:**
- Modify: `vibetrading/agent/src/agent/loop.py`
- Modify: `integrations/trade_integrations/autonomous_agents/turns.py`

**Interfaces:**
- Consumes: `session.config.autonomous_agent_id`, turn prompt `turn_kind`
- Produces: post-turn validator (widget guard pattern) — if autonomous turn ends without `record_autonomous_decision`, inject retry system nudge or mark attempt failed with "missing decision tool"

- [ ] **Step 1: Extend `_maybe_widget_guard` pattern** → `_maybe_autonomous_decision_guard`

- [ ] **Step 2: Required tools for bootstrap: `get_autonomous_agent_status`, `record_autonomous_decision`**

- [ ] **Step 3: Commit** — `feat(autonomous): enforce record_autonomous_decision on scheduler turns`

---

## Verification checklist (manual)

After Tasks 1–7 on a fresh NIFTY paper agent:

1. Confirm card → one bootstrap user turn → structured Decision block (not audit prose)
2. Card shows `conf 40%` and `last HOLD` immediately after turn
3. After market close: **no** new chat lines every 7 min; card `watch` timestamp still updates
4. During market hours with no alert: at most one watch summary per interval, preferably on card only
5. Fire real or test `REVIEW_NEEDED` alert → exactly one revision turn; no duplicate while streaming
6. `agent.thesis` JSON contains direction/strategy/confidence after HOLD
7. Chat contains no "synthetic alert", "handoff cycle", or "cached context" phrasing

Run: `python scripts/verify_autonomous_integration.py` (existing) + targeted pytest for new tests.

---

## Priority order

| Priority | Task | User impact |
|----------|------|-------------|
| P0 | Task 1 (thesis persist) | Card confidence; stops repeat re-analysis |
| P0 | Task 5 (output template) | Fixes confusing chat prose |
| P1 | Task 3 (watch spam) | Stops autonomous tab noise |
| P1 | Task 4 (scheduler seq) | Stops back-to-back HOLD turns |
| P1 | Task 2 (streaming dedupe) | Prevents duplicate alert turns |
| P2 | Task 6 (memory isolation) | Stops dev jargon leakage |
| P2 | Task 7 (tool enforcement) | Ensures decisions always recorded |

---

## Out of scope (follow-on)

- Parallel TradingAgents kick from research scheduler (design gap; separate plan)
- Full `AutonomousDecisionCard` React component with payoff preview on HOLD (nice-to-have)
- Ranker calibration from outcome ledger (Phase 3 remaining work)
