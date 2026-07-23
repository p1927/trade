# Phase 1: OpenAlgo MarketContext API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Depends on [Phase 0](./2026-07-23-market-authority-phase-0-connector.md) completion gate.

**Goal:** Add authoritative `POST /api/v1/marketcontext` so consumers stop inferring paper/live/broker/simulator from scattered sources.

**Architecture:** Extend OpenAlgo service layer; compose from `brokerinfo` + `analyze_mode` + stock_simulator status. Vibe OpenAlgo SDK adds `market_context()`; Trade adds thin client in `trade_integrations/openalgo/market_context.py`.

**Tech Stack:** Flask-RESTX, OpenAlgo services, Python requests, pytest.

## Global Constraints

- Backward compatible: keep `/api/v1/brokerinfo` unchanged.
- Single-user instance: global analyze_mode (no per-agent scope in API).
- Include `context_generation` ISO timestamp for cache invalidation.
- When `broker != stock_simulator`, `simulator` block is `{ "active": false }`.

---

### Task 1: MarketContext schema + service

**Files:**
- Create: `openalgo/services/marketcontext_service.py`
- Create: `openalgo/restx_api/marketcontext.py`
- Modify: `openalgo/restx_api/__init__.py`
- Test: `openalgo/test/test_marketcontext_service.py`

**Produces:**

```python
def get_marketcontext(api_key: str) -> tuple[bool, dict, int]:
    """Returns success, payload, status_code."""
```

**Response shape:**

```json
{
  "status": "success",
  "data": {
    "context_generation": "2026-07-23T09:15:00+05:30",
    "data_broker": "zerodha",
    "execution_venue": "sandbox",
    "analyze_mode": true,
    "market_region": "IN",
    "positions_authority": "sandbox.db",
    "quotes_source": "broker_plugin",
    "simulator": {"active": false},
    "capabilities": ["options", "equity", "basket", "websocket"]
  }
}
```

**Rules:**
- `execution_venue`: `"sandbox"` if analyze_mode else `"broker"`
- `positions_authority`: `"sandbox.db"` or `"broker"`
- `market_region`: `"IN"` for IN_stock brokers; `"US"` only when broker supports US (future alpaca); default `"IN"`
- When broker is `stock_simulator`: merge replay status from existing simulator status helper (reuse sandbox blueprint logic, not session-only route)

- [ ] **Step 1:** Write failing service tests (analyze on/off, stock_simulator broker)
- [ ] **Step 2:** Implement `get_marketcontext`
- [ ] **Step 3:** Wire RESTX namespace `POST /api/v1/marketcontext`
- [ ] **Step 4:** Run tests

```bash
cd openalgo && uv run pytest test/test_marketcontext_service.py -v
```

- [ ] **Step 5:** Convergence gate (Bugbot — focus: wrong positions_authority when analyze off)
- [ ] **Step 6:** Commit: `feat(openalgo): add marketcontext service and API`

---

### Task 2: Trade client wrapper

**Files:**
- Create: `integrations/trade_integrations/openalgo/market_context.py`
- Test: `tests/test_openalgo_market_context.py`

**Produces:**

```python
@dataclass(frozen=True)
class MarketContext:
    context_generation: str
    data_broker: str
    execution_venue: str
    analyze_mode: bool
    market_region: str
    positions_authority: str
    simulator: dict[str, Any]

def fetch_market_context(*, host: str, api_key: str, timeout: float = 20.0) -> MarketContext: ...
```

- [ ] **Step 1:** Failing test with mocked HTTP response
- [ ] **Step 2:** Implement parser + validation (reject partial payloads)
- [ ] **Step 3:** Convergence gate
- [ ] **Step 4:** Commit: `feat(trade): OpenAlgo MarketContext client`

---

### Task 3: Vibe OpenAlgo SDK market_context()

**Files:**
- Modify: `vibetrading/agent/src/trading/connectors/openalgo/sdk.py`
- Test: `vibetrading/agent/tests/test_sdk_connectors.py`

- [ ] **Step 1:** Add `_marketcontext(client)` + public method on connector class
- [ ] **Step 2:** Test paper vs live profile expectations (read-only check, no toggle)
- [ ] **Step 3:** Convergence gate
- [ ] **Step 4:** Commit: `feat(vibe): OpenAlgo SDK market_context()`

---

### Task 4: MCP + brokerinfo cross-link (optional doc in OpenAPI only)

**Files:**
- Modify: `openalgo/mcp/mcpserver.py` — add `market_context` tool wrapping same service
- Test: smoke in `openalgo/test/test_marketcontext_service.py`

- [ ] **Step 1:** MCP tool returns same payload as REST
- [ ] **Step 2:** Convergence gate
- [ ] **Step 3:** Commit: `feat(openalgo): MCP market_context tool`

---

## Phase 1 verification

```bash
cd openalgo && uv run pytest test/test_marketcontext_service.py -v
pytest tests/test_openalgo_market_context.py -v
cd ../vibetrading/agent && uv run pytest tests/test_sdk_connectors.py -k market_context -v
```

Live smoke (stack up):

```bash
curl -s -X POST http://127.0.0.1:5001/api/v1/marketcontext -H "Content-Type: application/json" -d '{"apikey":"'$OPENALGO_API_KEY'"}' | python -m json.tool
```

**Phase completion:** all tests pass + curl returns valid `data` block + Bugbot clean.

## Design-intent debate (pre-implementation)

| Question | Decision |
|----------|----------|
| Why not extend brokerinfo? | Separate endpoint avoids breaking MCP/SDK consumers; richer schema |
| Why global analyze_mode? | Matches OpenAlgo single-user design; multi-mode = multiple instances |
