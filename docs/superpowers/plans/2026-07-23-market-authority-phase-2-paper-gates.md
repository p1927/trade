# Phase 2: Collapse Paper Gates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Depends on [Phase 1](./2026-07-23-market-authority-phase-1-marketcontext.md) completion gate.

**Goal:** Replace scattered `ensure_analyzer_mode` / business use of `OPENALGO_PAPER_MODE` with MarketContext verification. Env lock stays ops-only; execution authority is OpenAlgo analyze_mode.

**Architecture:** Introduce `verify_execution_context(agent, market_context)` in Trade execution layer. Call sites replace toggle-with-side-effects with verify-or-fail-or-sync-once. Agent `constraints.mode` = prompt intent only.

**Tech Stack:** Python, pytest, OpenAlgo MarketContext client from Phase 1.

## Global Constraints

- **Do not remove** `OPENALGO_PAPER_MODE` deploy guard — demote to ops block on live execution only.
- Bridge preflight may still force analyzer when env lock ON **and** mandate is paper — but must log + stamp reason.
- Never silently proceed when agent mandate is paper and OpenAlgo is live (fail loud in autonomous path).

---

### Task 1: execution_context verification module

**Files:**
- Create: `integrations/trade_integrations/execution/context_verify.py`
- Test: `tests/test_execution_context_verify.py`

**Produces:**

```python
@dataclass(frozen=True)
class ContextVerification:
    ok: bool
    reason: str
    action_taken: str | None  # "none" | "analyzer_enabled" | "blocked"

def verify_agent_execution_context(
    *,
    agent: dict,
    market_context: MarketContext,
    env_paper_lock: bool,
    allow_analyzer_sync: bool = False,
) -> ContextVerification: ...
```

**Rules:**
- `agent.constraints.mode == "paper"` + `analyze_mode false` → `ok=False` unless `allow_analyzer_sync` and env lock allows sync
- `env_paper_lock` + live execution attempt → block regardless of UI
- `agent.constraints.mode == "live"` + analyze on → warn in autonomous (paper fills) — `ok=True` with reason (intent mismatch)

- [ ] **Step 1:** Failing tests for matrix above
- [ ] **Step 2:** Implement
- [ ] **Step 3:** Convergence gate (Bugbot — silent success paths)
- [ ] **Step 4:** Commit: `feat(execution): MarketContext verification module`

---

### Task 2: Migrate nautilus preflight

**Files:**
- Modify: `integrations/nautilus_openalgo_bridge/preflight.py`
- Test: `tests/test_nautilus_preflight.py`

- [ ] **Step 1:** Update tests — preflight calls `fetch_market_context` (mocked) not raw analyze toggle alone
- [ ] **Step 2:** Replace ad-hoc analyze logic with `verify_agent_execution_context`
- [ ] **Step 3:** Keep env `paper_only` as `env_paper_lock` input only
- [ ] **Step 4:** Convergence gate
- [ ] **Step 5:** Commit: `refactor(bridge): preflight uses MarketContext verify`

---

### Task 3: Migrate auto_paper + trade_routes

**Files:**
- Modify: `integrations/trade_integrations/auto_paper/mcp_actions.py`
- Modify: `integrations/trade_integrations/auto_paper/openalgo_client.py`
- Modify: `vibetrading/agent/src/api/trade_routes.py`
- Test: existing auto_paper + trade route tests

- [ ] **Step 1:** Audit all `ensure_analyzer_mode` callsites (grep)
- [ ] **Step 2:** Replace business routing with verify; keep single sync entry in `openalgo_client.ensure_analyzer_mode` only when verify says `allow_analyzer_sync`
- [ ] **Step 3:** Update tests for fail-loud on mismatch
- [ ] **Step 4:** Convergence gate (critical — order path)
- [ ] **Step 5:** Commit: `refactor(auto-paper): MarketContext-first paper gates`

---

### Task 4: Demote OPENALGO_PAPER_MODE in connector_context

**Files:**
- Modify: `integrations/trade_integrations/execution/connector_context.py`
- Test: `tests/test_connector_context.py`

- [ ] **Step 1:** Document in module docstring: env affects default profile inference only, not runtime market
- [ ] **Step 2:** Remove any business routing that reads env beyond default profile (if present)
- [ ] **Step 3:** Convergence gate
- [ ] **Step 4:** Commit: `refactor(execution): demote OPENALGO_PAPER_MODE in connector_context`

---

### Task 5: Cleanup dead code scan

**Files:**
- Grep-driven; remove only confirmed-dead paths after tests pass

- [ ] **Step 1:** Grep `ensure_analyzer_mode|_paper_mode_env_enabled|paper_only` — each callsite labeled KEPT (ops) vs REMOVED
- [ ] **Step 2:** Remove duplicate toggles only with test proof
- [ ] **Step 3:** Convergence gate on full diff
- [ ] **Step 4:** Commit: `chore(execution): remove redundant analyzer toggles`

---

## Phase 2 verification

```bash
pytest tests/test_execution_context_verify.py tests/test_nautilus_preflight.py tests/test_connector_context.py -q --timeout=120
# grep audit: no business routing on OPENALGO_PAPER_MODE outside trade_routes env lock + bridge config
rg "OPENALGO_PAPER_MODE" integrations/ vibetrading/ --glob "*.py" -l
```

**Failure-mode tests required:**
- Agent paper + OpenAlgo live → autonomous execute blocked
- Env lock on + live attempt → blocked
- Agent paper + OpenAlgo live + allow sync → analyzer enabled once, logged

**Phase completion:** matrix tests pass + grep audit documented in progress ledger + Bugbot clean.

## Mistake patterns checklist (Pass 3 manual)

| Pattern | Check |
|---------|-------|
| Silent success | `"skipped"` still returns ok? |
| Fix in one writer | grep all `ensure_analyzer_mode` |
| Happy-path-only | failure-mode tests above exist |
| Status rollup | preflight returns ok when verify failed? |
