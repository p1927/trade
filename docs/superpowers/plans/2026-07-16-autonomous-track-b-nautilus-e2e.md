# Autonomous Track B — Nautilus ↔ OpenAlgo E2E Loop (Finish) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove and harden the full autonomous loop: Vibe entry → handoff → Nautilus watch → alert → Vibe revision → EXIT via OpenAlgo.

**Architecture:** Bridge package exists (`integrations/nautilus_openalgo_bridge/`). Scheduler watch ticks call `poll_loop.run_once`. This track closes M1→M3 integration gaps and multi-agent watch binding.

**Tech Stack:** NautilusTrader 3.12+ venv, OpenAlgo REST, Vibe sessions, `autonomous_agents/nautilus_watch.py`, pytest integration markers.

**Depends on:** `2026-07-16-nautilus-openalgo-bridge.md` (Phases 1–5 largely done).

## Global Constraints

- **Execution authority:** OpenAlgo only. Nautilus emits intents; `execute.py` calls OpenAlgo.
- **Paper first:** Analyzer/sandbox until explicit live mandate.
- **Reconciliation:** OpenAlgo `positionbook` wins on conflict.
- Mark integration tests `@pytest.mark.integration` — require OpenAlgo + Vibe running.

---

### Task 1: Toolchain verify script

**Files:**
- Create: `scripts/verify_nautilus_toolchain.py`
- Modify: `scripts/setup_nautilus.sh` (if gaps)

**Interfaces:**
- Produces: exit 0 when `import nautilus_trader`, bridge models, and OpenAlgo ping succeed

- [ ] **Step 1:** Script checks Python 3.12+, nautilus import, `OPENALGO_HOST` health
- [ ] **Step 2:** Document in plan verification block
- [ ] **Step 3:** Commit

---

### Task 2: M1 smoke — watch alert → Vibe message

**Files:**
- Create: `scripts/verify_nautilus_m1_watch_alert.py`
- Modify: `integrations/nautilus_openalgo_bridge/vibe_trigger.py` (dedupe if needed)

**Interfaces:**
- Consumes: synthetic spot move in watch eval OR test agent with low threshold
- Produces: Vibe session receives alert message; logged decision stub

- [ ] **Step 1:** Script fires synthetic alert for test agent
- [ ] **Step 2:** Assert message appended to session store (mock or live)
- [ ] **Step 3:** Commit

---

### Task 3: Handoff reload on file change

**Files:**
- Modify: `integrations/nautilus_openalgo_bridge/watch_actor.py` or `runtime/poll_loop.py`
- Test: `tests/test_nautilus_handoff_reload.py`

**Interfaces:**
- Produces: WatchActor re-reads handoff JSON when mtime changes

- [ ] **Step 1:** Write unit test with temp handoff file
- [ ] **Step 2:** Implement mtime watch in poll loop
- [ ] **Step 3:** Run tests; commit

---

### Task 4: M3 E2E harness extension

**Files:**
- Modify: `scripts/run_realistic_agent_cycle_e2e.py` or `scripts/realistic_e2e_lib.py`
- Modify: `integrations/nautilus_openalgo_bridge/handoff.py` hook after basket success

**Interfaces:**
- Produces: documented M3 path: create agent → entry basket → handoff file → watch tick → EXIT intent

- [ ] **Step 1:** Extend e2e lib with handoff assertion step
- [ ] **Step 2:** Wire EXIT reconcile to `outcome_ledger` on bridge execute
- [ ] **Step 3:** Manual runbook in plan verification; commit

---

### Task 5: Multi-agent watch registry (optional v1)

**Files:**
- Modify: `integrations/trade_integrations/autonomous_agents/nautilus_watch.py`
- Modify: `integrations/nautilus_openalgo_bridge/runtime/run_watch_node.py`

**Interfaces:**
- Produces: `ensure_nautilus_watch_for_agent` restarts node with updated agent list OR documents single-agent limitation

- [ ] **Step 1:** Document current single-PID behavior OR implement agent registry file
- [ ] **Step 2:** `trade status` shows bound agent_id
- [ ] **Step 3:** Commit

---

## Verification

```bash
python3 scripts/verify_nautilus_toolchain.py
pytest tests/test_nautilus_*.py -v -m "not integration"
# Market hours:
python3 scripts/verify_nautilus_m1_watch_alert.py --agent-id aa_xxx
```

M3 manual: `python3 scripts/run_realistic_agent_cycle_e2e.py --symbol NIFTY --full-loop`
