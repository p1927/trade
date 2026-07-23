# Phase 3: TradingConnectorPort — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Depends on [Phase 1](./2026-07-23-market-authority-phase-1-marketcontext.md) and [Phase 2](./2026-07-23-market-authority-phase-2-paper-gates.md).

**Goal:** Single port interface for quotes, orders, positions, and market context. Migrate bridge, auto_paper, and autonomous agents off ad-hoc OpenAlgo/Alpaca clients.

**Architecture:** Hexagonal port in `integrations/trade_integrations/execution/trading_port.py`. `OpenAlgoConnectorAdapter` implements IN path; `AlpacaConnectorAdapter` implements US path until Phase 4. Vibe MCP tools delegate to same port via thin HTTP or shared module.

**Tech Stack:** Python Protocol/ABC, pytest, existing connector_context.

## Global Constraints

- No Vibe → Trade circular imports. Port lives in Trade; Vibe may duplicate Protocol shape or import Trade package if PYTHONPATH allows (verify in Task 1).
- `ExecutionProfile` becomes derived: `profile_from_context(market_context, agent)`.
- Handoff JSON gains `context_generation` field.

---

### Task 1: Port definition + OpenAlgo adapter

**Files:**
- Create: `integrations/trade_integrations/execution/trading_port.py`
- Create: `integrations/trade_integrations/execution/adapters/openalgo_adapter.py`
- Test: `tests/test_trading_port_openalgo.py`

**Produces:**

```python
class TradingConnectorPort(Protocol):
    def market_context(self) -> MarketContext: ...
    def quote(self, symbol: str, exchange: str = "NSE") -> dict | None: ...
    def quotes_batch(self, requests: list[dict]) -> dict[str, dict]: ...
    def positionbook(self) -> list[dict]: ...
    def place_basket(self, legs: list[dict], **kwargs) -> dict: ...

def adapter_for_agent(agent: dict) -> TradingConnectorPort: ...
```

- [ ] **Step 1:** Protocol + OpenAlgo adapter wrapping existing clients
- [ ] **Step 2:** Tests with mocked HTTP
- [ ] **Step 3:** Convergence gate
- [ ] **Step 4:** Commit: `feat(execution): TradingConnectorPort + OpenAlgo adapter`

---

### Task 2: Alpaca adapter (interim US path)

**Files:**
- Create: `integrations/trade_integrations/execution/adapters/alpaca_adapter.py`
- Test: `tests/test_trading_port_alpaca.py`

- [ ] **Step 1:** Wrap `dataflows/alpaca.py` behind port (connector profile id → env credentials)
- [ ] **Step 2:** `adapter_for_agent` routes US agents to Alpaca adapter
- [ ] **Step 3:** Convergence gate
- [ ] **Step 4:** Commit: `feat(execution): Alpaca TradingConnectorPort adapter`

---

### Task 3: Derive ExecutionProfile from MarketContext

**Files:**
- Modify: `integrations/trade_integrations/execution/profile.py`
- Test: `tests/test_execution_profile.py` (extend or create)

- [ ] **Step 1:** Add `resolve_profile_from_context(agent, market_context)` 
- [ ] **Step 2:** Keep `resolve_profile()` as wrapper: fetch context + derive (backward compat)
- [ ] **Step 3:** Convergence gate
- [ ] **Step 4:** Commit: `refactor(execution): derive profile from MarketContext`

---

### Task 4: Bridge handoff context stamp

**Files:**
- Modify: `integrations/nautilus_openalgo_bridge/handoff.py`
- Modify: `integrations/trade_integrations/autonomous_agents/nautilus_watch.py`
- Test: `tests/test_nautilus_vibe_trigger.py` or handoff tests

- [ ] **Step 1:** Handoff schema adds optional `context_generation: str`
- [ ] **Step 2:** Watch start fetches MarketContext once, stamps handoff
- [ ] **Step 3:** Preflight rejects stale context (> N minutes) with reload
- [ ] **Step 4:** Convergence gate
- [ ] **Step 5:** Commit: `feat(bridge): stamp context_generation on handoff`

---

### Task 5: Migrate market_quotes + bridge client

**Files:**
- Modify: `integrations/trade_integrations/dataflows/market_quotes.py`
- Modify: `integrations/nautilus_openalgo_bridge/openalgo_client.py`
- Test: phase 0 + bridge tests

- [ ] **Step 1:** `market_quotes` uses `adapter_for_agent` when agent dict available
- [ ] **Step 2:** Bridge OpenAlgo client delegates quote fetch to port where feasible
- [ ] **Step 3:** Convergence gate
- [ ] **Step 4:** Commit: `refactor: migrate quotes to TradingConnectorPort`

---

### Task 6: Fix misleading US connector → alpaca backend map

**Files:**
- Modify: `integrations/trade_integrations/execution/connector_context.py`
- Test: `tests/test_connector_context.py`

- [ ] **Step 1:** Add `execution_path: Literal["openalgo","alpaca_sdk","connector_sdk"]` to context
- [ ] **Step 2:** IBKR/Tiger/etc. map to `connector_sdk` not `alpaca` backend
- [ ] **Step 3:** Update status fields in `mcp_actions.py` to use `execution_path`
- [ ] **Step 4:** Convergence gate
- [ ] **Step 5:** Commit: `fix(execution): honest connector execution_path labels`

---

## Phase 3 verification

```bash
pytest tests/test_trading_port_openalgo.py tests/test_trading_port_alpaca.py tests/test_connector_context.py tests/test_nautilus_preflight.py tests/test_market_quotes_connector.py -q --timeout=120
```

**Phase completion:** port adapters tested + handoff stamps context + Bugbot clean + final branch reviewer.

## Cleanup targets (remove after migration proven)

- Direct `fetch_openalgo_quote` in agent turn paths where port exists
- Duplicate market resolution in `poll_loop` US path (use adapter)

**Do not remove yet:** `dataflows/alpaca.py` core (Phase 4 may relocate into OpenAlgo plugin)
