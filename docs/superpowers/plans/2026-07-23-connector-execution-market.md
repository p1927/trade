# Connector-Driven Execution Market (OpenAlgo / Alpaca / Simulator)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or executing-plans.

**Goal:** Resolve autonomous agent `execution_market` and backend from the **selected trading connector profile** (`~/.vibe-trading/trading-connections.json`), not symbol heuristics alone. OpenAlgo connector → IN; Alpaca connector → US; stock simulator → IN replay via OpenAlgo.

**Architecture:** New `execution/connector_context.py` reads selected profile id, maps `connector` key → `(market, backend)`. `market_resolve.py` prefers connector context before symbol registry. Proposals/agents store `connector_profile_id`; symbol validation checks allowed market for connector.

**Tech Stack:** Python, existing `market_resolve`, `proposals`, `strategy_progress`.

## Global Constraints

- Do not import full Vibe agent stack from `trade_integrations` — read JSON + static connector map only.
- Agent-stored `execution_market` remains explicit override when set at propose time.
- stock_simulator active forces IN regardless of profile label.

---

### Task 1: connector_context module

- [ ] `load_active_connector_context()` from runtime JSON
- [ ] `connector_execution_market(connector)` map + simulator override
- [ ] Tests with temp runtime dir

### Task 2: market resolution

- [ ] `resolve_execution_market` uses connector before symbol registry when no explicit hint
- [ ] `agent_execution_market` reads `connector_profile_id` on agent
- [ ] Update `validate_proposal_routing` / symbol validation for connector market

### Task 3: Proposals + agents

- [ ] Store `connector_profile_id` on proposal and committed agent
- [ ] Default `execution_market` from connector in `propose_autonomous_agent`

### Task 4: strategy_progress US path

- [ ] Document connector-scoped Alpaca positions (single connector session per runtime)

### Verify

```bash
pytest tests/test_market_resolve.py tests/test_autonomous_market.py tests/test_connector_context.py -q
```
