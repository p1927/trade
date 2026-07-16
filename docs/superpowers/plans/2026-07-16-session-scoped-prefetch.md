# Session-Scoped Prefetch & Memory Separation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Keep Vibe Trading hub prefetch and persistent memory, but scope research and recall to the session's symbol/market so NIFTY and SPY never mix in one autonomous turn.

**Architecture:** Add `resolve_prefetch_context(session_config, content)` that prefers `session.config.symbols[0]` and `execution_market` over message-regex ticker detection. Filter auto-recall by market. Clarify autonomous session instructions so prefetched hub blocks are authoritative context, not injection.

**Tech Stack:** Python (`hub_bridge.py`, `session_context.py`, `context.py`, `symbol_detect.py`, `widget_intent.py`), E2E scripts (`realistic_e2e_lib.py`, `run_realistic_agent_cycle_e2e.py`).

---

### Task 1: Session-scoped prefetch ticker resolution

**Files:**
- Create: `vibetrading/agent/src/trade/session_context.py`
- Modify: `vibetrading/agent/src/trade/hub_bridge.py`
- Modify: `vibetrading/agent/src/session/service.py`
- Test: `vibetrading/agent/tests/test_session_context.py`

**Steps:**
1. Add `resolve_prefetch_ticker(session_config, content)` — autonomous_agent → `symbols[0]`; else message fallback.
2. Add `infer_prefetch_asset_type(session_config, ticker, content)` — US equity → stock; IN index → options/index.
3. Add `classify_prefetch_widget_intent(session_config, content)` — autonomous US equity → stock_trade not execute_refresh on bare "execution".
4. Pass `session_config` through `prefetch_research_for_message`.
5. Unit tests for SPY session + NIFTY-in-preamble, NIFTY session unchanged.

---

### Task 2: E2E preamble — remove cross-market ticker literals

**Files:**
- Modify: `scripts/realistic_e2e_lib.py`

**Steps:**
1. Replace `(e.g. NIFTY)` with generic "other agents or markets" wording.

---

### Task 3: Market-filtered persistent memory recall

**Files:**
- Modify: `vibetrading/agent/src/trade/session_context.py`
- Modify: `vibetrading/agent/src/agent/context.py`
- Test: extend `test_session_context.py`

**Steps:**
1. Add `memory_matches_session(entry, session_config)` — skip IN-only memories in US sessions and vice versa.
2. Apply filter in `ContextBuilder.build_messages` before injecting `<recalled-memories>`.

---

### Task 4: Autonomous session system note — prefetch authority

**Files:**
- Modify: `integrations/trade_integrations/autonomous_agents/proposals.py`

**Steps:**
1. Extend US and IN `system_note` to state hub `[research_context]` for this session's symbol is normal prefetch; trust `get_autonomous_agent_status` on conflict.

---

### Task 5: E2E harness — full mandate on Phase 2 + tool assertion

**Files:**
- Modify: `scripts/run_realistic_agent_cycle_e2e.py`
- Modify: `scripts/realistic_e2e_lib.py`

**Steps:**
1. Phase 2 includes `build_full_reasoning_prompt` + phase delta.
2. Add `assert_turn_tools_or_fail` helper; Phase 2 US requires `trading_place_order` (warn/fail if mechanical fallback used).

---

### Task 6: Widget guard session ticker (optional parity)

**Files:**
- Modify: `vibetrading/agent/src/trade/widget_guard.py`
- Modify: `vibetrading/agent/src/session/service.py`

**Steps:**
1. Pass session_config to widget guard; use session ticker when available.

---

## Verification

```bash
cd vibetrading/agent && python -m pytest tests/test_session_context.py -q
cd ../.. && python -m pytest vibetrading/agent/tests/test_orchestrator_profile.py -q
```

Manual: run US E2E and confirm prefetch log/context shows SPY only, no NIFTY blocks prepended.
