# Phase 4: Alpaca OpenAlgo Broker Plugin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Depends on [Phase 3](./2026-07-23-market-authority-phase-3-trading-port.md). **Large phase — split into sub-PRs if needed.**

**Goal:** US equities flow through OpenAlgo like India — one REST surface, one MarketContext, one position authority path.

**Architecture:** New `openalgo/broker/alpaca/` plugin following Zerodha/Dhan pattern: `api/data.py`, `api/order_api.py`, `api/funds.py`, `mapping/`, `streaming/`, `plugin.json`. Trade US agents use OpenAlgo adapter only; deprecate direct Alpaca SDK for agent execution paths.

**Tech Stack:** OpenAlgo broker plugin system, Alpaca REST/WebSocket, pytest.

## Global Constraints

- Paper and live Alpaca hosts via plugin config (paper-api.alpaca.markets vs api.alpaca.markets).
- OpenAlgo analyze_mode still routes IN orders to sandbox; for Alpaca plugin define: US paper can use Alpaca paper account OR OpenAlgo sandbox — **decision: Alpaca paper account for US when broker=alpaca and analyze_mode=true maps to Alpaca paper credentials** (document in plugin README comment only, not user-facing doc file).
- Keep `dataflows/alpaca.py` for offline research/backfill until explicitly removed.

## Reference implementation

Copy structure from `openalgo/broker/dhan/` or `openalgo/broker/zerodha/` — thinnest viable adapter.

---

### Task 1: Plugin scaffold + auth

**Files:**
- Create: `openalgo/broker/alpaca/plugin.json`
- Create: `openalgo/broker/alpaca/api/auth_api.py`
- Create: `openalgo/broker/alpaca/database/` (minimal token storage)
- Modify: `.env.example` — `VALID_BROKERS` includes alpaca
- Test: `openalgo/test/test_alpaca_plugin_auth.py`

- [ ] **Step 1:** plugin.json metadata (`broker_type: US_equity`, exchanges NASDAQ/NYSE)
- [ ] **Step 2:** API key auth (no OAuth) — store key/secret from env or login form
- [ ] **Step 3:** Convergence gate
- [ ] **Step 4:** Commit: `feat(openalgo): alpaca broker plugin scaffold`

---

### Task 2: Market data (quotes, history)

**Files:**
- Create: `openalgo/broker/alpaca/api/data.py`
- Create: `openalgo/broker/alpaca/mapping/order_data.py`
- Test: `openalgo/test/test_alpaca_data.py`

- [ ] **Step 1:** Implement quotes + history normalizing to OpenAlgo response shape
- [ ] **Step 2:** Wire `/api/v1/quotes` integration test (mock httpx)
- [ ] **Step 3:** Convergence gate
- [ ] **Step 4:** Commit: `feat(openalgo): alpaca data adapter`

---

### Task 3: Orders + positions

**Files:**
- Create: `openalgo/broker/alpaca/api/order_api.py`
- Create: `openalgo/broker/alpaca/api/funds.py`
- Test: `openalgo/test/test_alpaca_orders.py`

- [ ] **Step 1:** placeorder, cancelorder, positionbook, orderbook
- [ ] **Step 2:** analyze_mode routing: when ON, route to sandbox OR Alpaca paper per Task 1 decision — implement consistently in `place_order_service.py` branch for alpaca broker
- [ ] **Step 3:** Convergence gate (order authority critical)
- [ ] **Step 4:** Commit: `feat(openalgo): alpaca order execution`

---

### Task 4: MarketContext US region

**Files:**
- Modify: `openalgo/services/marketcontext_service.py`
- Test: extend `openalgo/test/test_marketcontext_service.py`

- [ ] **Step 1:** When broker=alpaca → `market_region: "US"`, capabilities include US equities
- [ ] **Step 2:** Convergence gate
- [ ] **Step 3:** Commit: `feat(openalgo): US MarketContext for alpaca broker`

---

### Task 5: Trade migration — US agents via OpenAlgo only

**Files:**
- Modify: `integrations/trade_integrations/execution/adapters/openalgo_adapter.py`
- Modify: `integrations/trade_integrations/execution/profile.py`
- Modify: `integrations/nautilus_openalgo_bridge/runtime/poll_loop.py` (US path)
- Test: `tests/test_market_resolve.py`, US agent integration tests

- [ ] **Step 1:** US profile sets `backend=openalgo`, `uses_openalgo_auto_paper=True` when Alpaca plugin available
- [ ] **Step 2:** Remove direct Alpaca poll path in bridge when OpenAlgo US available (feature flag `OPENALGO_US_VIA_PLUGIN=1`)
- [ ] **Step 3:** Convergence gate
- [ ] **Step 4:** Commit: `feat(execution): route US agents through OpenAlgo alpaca plugin`

---

### Task 6: Deprecation markers

**Files:**
- Modify: `integrations/trade_integrations/dataflows/alpaca.py` — module docstring DEPRECATED for agent paths
- Modify: `integrations/trade_integrations/execution/adapters/alpaca_adapter.py` — gate behind flag; default off after Task 5

- [ ] **Step 1:** Grep agent execution callsites — all through port → OpenAlgo
- [ ] **Step 2:** Convergence gate on full phase diff
- [ ] **Step 3:** Commit: `chore: deprecate direct Alpaca agent execution path`

---

## Phase 4 verification

```bash
cd openalgo && uv run pytest test/test_alpaca_plugin_auth.py test/test_alpaca_data.py test/test_alpaca_orders.py test/test_marketcontext_service.py -v
pytest tests/test_market_resolve.py tests/test_trading_port_openalgo.py -q --timeout=120
```

Live smoke (Alpaca paper configured):

```bash
# Login broker alpaca in OpenAlgo UI, then:
curl -s -X POST http://127.0.0.1:5001/api/v1/marketcontext -d '{"apikey":"..."}' 
curl -s -X POST http://127.0.0.1:5001/api/v1/quotes -d '{"apikey":"...","symbol":"AAPL","exchange":"NASDAQ"}'
```

**Phase completion:** US quotes/positions via OpenAlgo + autonomous US agent status shows openalgo backend + Bugbot clean.

## Risk register

| Risk | Mitigation |
|------|------------|
| Dual US paths during migration | Feature flag `OPENALGO_US_VIA_PLUGIN` |
| analyze_mode semantics differ IN vs US | Explicit in marketcontext `execution_venue` |
| Rate limits | Reuse httpx client pooling |
