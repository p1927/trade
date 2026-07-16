# Autonomous Track A — Create Agent & Orchestrator (Finish) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close remaining gaps in `/autonomous` Create agent flow so orchestrator reliably produces proposal cards and running agents never confuse users with orchestrator-style questions.

**Architecture:** Orchestrator sessions use isolated brain (dedicated prompt, tool allowlist, no prefetch/widget guard). Running `aa_*` sessions use `turns.py` prompts with decision-only footer. Proposal store marks superseded cards when re-proposed in same session.

**Tech Stack:** Vibe `ContextBuilder`, `orchestrator_profile.py`, `proposals.py`, `store.py`, `turns.py`, React `AutonomousAgentProposalCard.tsx`, pytest.

**Depends on:** `2026-07-16-create-agent-session-lifecycle.md` (done), `2026-07-16-orchestrator-ux-holistic.md` (Phases 1–4 done).

## Global Constraints

- Orchestrator **never executes trades** — propose only; commit is UI-only (`consent_ack`).
- Reuse `session_kind`: `autonomous_orchestrator` → `autonomous_agent` on promote.
- Max **10 concurrent** running/paused agents.
- Proposal TTL **30 min** (`PROPOSAL_TTL_MS`).
- No new summary docs beyond this plan.

---

### Task 1: Auto-inject orchestrator skill in system prompt

**Files:**
- Modify: `vibetrading/agent/src/agent/context.py`
- Test: `tests/test_orchestrator_context.py` (create)

**Interfaces:**
- Consumes: `SkillsLoader.load("autonomous-orchestrator")` or read `src/skills/autonomous-orchestrator/SKILL.md`
- Produces: `ContextBuilder.build_system_prompt()` appends full orchestrator skill body when `session_kind=autonomous_orchestrator`

- [ ] **Step 1:** Write test — orchestrator prompt contains skill workflow text ("propose_autonomous_agent")
- [ ] **Step 2:** Implement `_orchestrator_skill_block()` in `context.py`; append to orchestrator base prompt
- [ ] **Step 3:** Run `pytest tests/test_orchestrator_context.py -v`
- [ ] **Step 4:** Commit

---

### Task 2: Running-agent decision-only footer

**Files:**
- Modify: `integrations/trade_integrations/autonomous_agents/turns.py`
- Test: `tests/test_autonomous_turns.py` (create or extend)

**Interfaces:**
- Produces: `build_full_reasoning_prompt()` ends with footer forbidding user questions; requires `record_autonomous_decision` on action turns

- [ ] **Step 1:** Write test — prompt contains "Do not ask the user" and `record_autonomous_decision`
- [ ] **Step 2:** Append `_RUNNING_AGENT_FOOTER` to `build_full_reasoning_prompt` return
- [ ] **Step 3:** Run tests
- [ ] **Step 4:** Commit

---

### Task 3: Supersede stale proposals on re-propose

**Files:**
- Modify: `integrations/trade_integrations/autonomous_agents/store.py`
- Modify: `vibetrading/agent/src/tools/propose_autonomous_agent_tool.py` (or `proposals.py`)
- Test: `tests/test_proposal_supersede.py`

**Interfaces:**
- Produces: `mark_superseded_proposals(orchestrator_session_id, except_proposal_id) -> int`
- Consumes: called from `save_proposal` / propose tool before saving new proposal

- [ ] **Step 1:** Write test — second propose marks first as `superseded: true`
- [ ] **Step 2:** Implement mark function; filter superseded in `load_latest_proposal_for_orchestrator`
- [ ] **Step 3:** Wire into propose tool
- [ ] **Step 4:** Run tests; commit

---

### Task 4: Orchestrator propose → commit integration test

**Files:**
- Create: `tests/test_orchestrator_propose_flow.py`

**Interfaces:**
- Consumes: `save_proposal`, `commit_autonomous_agent`, `promote_orchestrator_session` (mocked session service)

- [ ] **Step 1:** Test propose saves proposal with session_id
- [ ] **Step 2:** Test commit promotes session and creates agent JSON
- [ ] **Step 3:** Run full track A pytest bundle
- [ ] **Step 4:** Commit

---

## Verification

```bash
pytest tests/test_orchestrator_context.py tests/test_autonomous_turns.py \
  tests/test_proposal_supersede.py tests/test_orchestrator_propose_flow.py \
  tests/test_session_promotion.py tests/test_commit_session_promotion.py -v
```

Manual: `/autonomous` → Create agent → propose card → Confirm → same session promotes to `aa_*`.
