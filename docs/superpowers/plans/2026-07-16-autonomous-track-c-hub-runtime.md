# Autonomous Track C — Hub Runtime & UI (Finish) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish autonomous hub observability — instant refresh on commit, EXIT outcomes feeding ranker calibration, stack health accuracy.

**Architecture:** `runtime_status.py` already powers API. Frontend hub polls + window events. This track wires SSE hub refresh and confirms EXIT → outcome_ledger → ranker path in production turns.

**Tech Stack:** FastAPI `autonomous_routes.py`, React `AutonomousAgentHub.tsx`, `outcome_ledger.py`, `hub_context.py`.

**Depends on:** `2026-07-16-autonomous-remaining-phases.md` (Phases 1–4 largely done).

## Global Constraints

- Reuse existing SSE `autonomous_agent.committed` event.
- No duplicate runtime APIs — extend `build_agent_runtime` only if fields missing.
- Paper calibration adjustments already in rankers — verify wiring only.

---

### Task 1: Hub direct SSE refresh

**Files:**
- Modify: `vibetrading/frontend/src/components/autonomous/AutonomousAgentHub.tsx`
- Modify: `vibetrading/frontend/src/pages/Autonomous.tsx`

**Interfaces:**
- Consumes: existing SSE stream from `Agent.tsx` or shared event bus
- Produces: hub refetches agent list on `autonomous_agent.committed` without 15s poll wait

- [ ] **Step 1:** Subscribe to `autonomous-agents-refresh` window event (already emitted) OR add EventSource listener on hub mount
- [ ] **Step 2:** Debounce refresh 300ms; preserve scroll position
- [ ] **Step 3:** Manual verify Confirm → card appears <1s
- [ ] **Step 4:** Commit

---

### Task 2: EXIT → outcome ledger on bridge execute

**Files:**
- Modify: `integrations/nautilus_openalgo_bridge/execute.py`
- Modify: `integrations/trade_integrations/auto_paper/outcome_ledger.py` (if hook missing)
- Test: `tests/test_outcome_ledger_calibration.py` (extend)

**Interfaces:**
- Produces: `record_exit_outcome(agent_id, strategy_name, pnl, ...)` called after successful EXIT intent

- [ ] **Step 1:** Write test — EXIT execute appends ledger row
- [ ] **Step 2:** Wire in `execute.py` post-flight reconcile
- [ ] **Step 3:** Run tests; commit

---

### Task 3: Runtime block completeness audit

**Files:**
- Modify: `integrations/trade_integrations/autonomous_agents/runtime_status.py` (only if gaps)
- Test: `tests/test_runtime_status.py`

**Interfaces:**
- Produces: `build_agent_runtime` includes `handoff_active`, `nautilus_watch`, `last_decision`, `debate_pending` when applicable

- [ ] **Step 1:** Test fixture agent with handoff → runtime.handoff_active true
- [ ] **Step 2:** Fix any missing fields found by test
- [ ] **Step 3:** Commit

---

## Verification

```bash
pytest tests/test_runtime_status.py tests/test_outcome_ledger_calibration.py -v
```

Manual: Confirm agent → hub card shows runtime dots; after paper EXIT, next research turn shows `[paper_calibration]` in hub context.
