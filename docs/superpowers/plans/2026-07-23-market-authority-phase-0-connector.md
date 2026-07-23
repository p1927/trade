# Phase 0: Connector-Driven Market Resolution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Follow verification protocol in [2026-07-23-openalgo-market-authority-index.md](./2026-07-23-openalgo-market-authority-index.md).

**Goal:** Finish connector-driven `execution_market` / backend resolution from `trading-connections.json`; eliminate remaining symbol-only bypass paths and duplicate default-profile inference.

**Architecture:** `connector_context.py` → `market_resolve.py` → `ExecutionProfile`. Agents store `connector_profile_id`. `market_quotes` respects active connector before symbol heuristics.

**Tech Stack:** Python, pytest, existing autonomous agents + execution modules.

## Global Constraints

- Do not import Vibe agent from `trade_integrations`.
- Explicit `execution_market` on agent remains override when set at propose time.
- stock_simulator active → IN.

## Current state (audit before Task 1)

**Already shipped (do not re-implement):**

- `integrations/trade_integrations/execution/connector_context.py` — full module
- `market_resolve.py` — connector before symbol registry
- `proposals.py` — stores `connector_profile_id`, loads connector context
- `tests/test_connector_context.py` — basic tests

**Gaps to close:**

- `market_quotes.fetch_live_quote` — still `detect_market()` only
- Duplicate `infer_default_profile_id` in Vibe vs Trade
- `store.py` agent template may null `connector_profile_id` on some paths
- No regression test: US connector selected + IN symbol → validation error at propose

---

### Task 1: Audit + mark baseline

**Files:**
- Read: `connector_context.py`, `market_resolve.py`, `proposals.py`, `market_quotes.py`
- Update: `.superpowers/sdd/progress.md`

- [ ] **Step 1:** Run baseline pytest

```bash
pytest tests/test_connector_context.py tests/test_market_resolve.py tests/test_autonomous_bootstrap.py -q --timeout=120
```

- [ ] **Step 2:** Document baseline pass/fail in progress ledger
- [ ] **Step 3:** Convergence gate (Bugbot on any doc-only change — skip if no code)

---

### Task 2: Wire market_quotes through connector context

**Files:**
- Modify: `integrations/trade_integrations/dataflows/market_quotes.py`
- Test: `tests/test_market_quotes_connector.py` (create)

**Interfaces:**
- Consumes: `load_active_connector_context()`, `connector_execution_market()`
- Produces: `fetch_live_quote(symbol, *, connector_context=None)` — when context says US, route Alpaca even if symbol looks IN; when IN, route OpenAlgo

- [ ] **Step 1: Write failing test**

```python
def test_fetch_live_quote_respects_us_connector_for_us_symbol(monkeypatch, tmp_path):
    # setup runtime JSON with alpaca-paper-sdk selected
    # mock alpaca fetch; assert called for AAPL
    ...

def test_fetch_live_quote_in_connector_blocks_us_symbol_routing_to_alpaca(monkeypatch, tmp_path):
    # openalgo selected; AAPL → no alpaca call (returns None or explicit error path)
    ...
```

- [ ] **Step 2:** Run test — expect FAIL

```bash
pytest tests/test_market_quotes_connector.py -v
```

- [ ] **Step 3:** Implement minimal routing in `market_quotes.py`:

```python
def fetch_live_quote(symbol: str, *, connector_context=None) -> dict | None:
    ctx = connector_context or _load_context_optional()
    market = _resolve_quote_market(symbol, ctx)
    ...
```

- [ ] **Step 4:** Run test — expect PASS
- [ ] **Step 5:** Convergence gate (Pass 2→3→4, min 2 rounds, Bugbot)
- [ ] **Step 6:** Commit: `feat(dataflows): route market_quotes via connector context`

---

### Task 3: Deduplicate infer_default_profile_id

**Files:**
- Modify: `integrations/trade_integrations/execution/connector_context.py`
- Modify: `vibetrading/agent/src/trading/profiles.py`
- Test: `tests/test_connector_context.py`, `vibetrading/agent/tests/test_api_trading_connectors.py`

**Approach:** Extract shared logic to `integrations/trade_integrations/execution/default_profile.py` (Trade-owned, no Vibe imports). Vibe `profiles.py` imports from Trade path via existing repo PYTHONPATH or duplicates via thin wrapper calling shared function — **prefer Trade module imported by Vibe** only if path already works; else copy-once with single test asserting both agree on fixture envs.

- [ ] **Step 1:** Write test comparing outputs for env matrix:

```python
@pytest.mark.parametrize("env", [
    {"OPENALGO_API_KEY": "k", "OPENALGO_PAPER_MODE": "true"},
    {"OPENALGO_API_KEY": "k", "OPENALGO_PAPER_MODE": "off"},
    {"ALPACA_API_KEY": "k", "ALPACA_API_SECRET": "s"},
])
def test_default_profile_parity(env, monkeypatch):
    ...
```

- [ ] **Step 2–4:** Implement shared module; wire both callers
- [ ] **Step 5:** Convergence gate
- [ ] **Step 6:** Commit: `refactor(execution): single default profile inference`

---

### Task 4: Agent store connector_profile_id persistence

**Files:**
- Modify: `integrations/trade_integrations/autonomous_agents/store.py`
- Test: extend `tests/test_autonomous_bootstrap.py` or `tests/test_orchestrator_session_create.py`

- [ ] **Step 1:** Failing test — committed agent retains `connector_profile_id` from proposal
- [ ] **Step 2–4:** Fix store template / merge paths that reset to `None`
- [ ] **Step 5:** Convergence gate
- [ ] **Step 6:** Commit: `fix(autonomous): persist connector_profile_id on agent store`

---

### Task 5: Proposal symbol validation regression

**Files:**
- Modify: `integrations/trade_integrations/autonomous_agents/proposals.py` (if gap found)
- Test: `tests/test_market_resolve.py`

- [ ] **Step 1:** Test — US connector + NIFTY symbol → validation error at propose
- [ ] **Step 2–4:** Ensure `validate_proposal_routing` / `symbol_allowed_for_connector_market` enforced
- [ ] **Step 5:** Convergence gate
- [ ] **Step 6:** Commit: `test(autonomous): block symbol/connector market mismatch`

---

## Phase 0 verification (full scope)

```bash
pytest tests/test_connector_context.py tests/test_market_quotes_connector.py tests/test_market_resolve.py tests/test_autonomous_bootstrap.py tests/test_orchestrator_session_create.py -q --timeout=120
```

**Phase completion:** exit 0 + phase Bugbot convergence clean + final whole-branch reviewer.

## Cleanup (explicitly out of scope for Phase 0)

- Do not remove `OPENALGO_PAPER_MODE` gates yet (Phase 2).
- Do not add MarketContext endpoint yet (Phase 1).
