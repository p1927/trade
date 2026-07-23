# Unified OpenAlgo Data Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One maintainable path for all India market-data reads — shared OpenAlgo REST client, hub channel as the single read facade, consistent freshness policies — so TradingAgents, Vibe MCP, Nautilus watch, and research pipelines stop duplicating HTTP calls and cache logic.

**Architecture:** Extract `trade_integrations/openalgo/` as the canonical layer (REST client + market-data fetchers + chain normalization). Extend `hub_capture/channel.py` with explicit `FreshnessPolicy` (LIVE / NORMAL / WATCH) and optional in-process L1 dedupe. All subscribers call channel helpers; channel calls OpenAlgo vendor functions; nselib fallback lives in one place. Execution (orders, positionbook) stays on existing bridge/MCP clients in Phase 1; WebSocket feed for Nautilus is Phase 2 (deferred).

**Tech Stack:** Python 3.12+, existing `hub_capture`, OpenAlgo REST `/api/v1/*`, TradingAgents vendor errors, pytest.

## Global Constraints

- **Execution authority unchanged:** OpenAlgo only; this plan covers **market-data reads**, not order routing.
- **OpenAlgo remains the broker gateway:** No direct INDmoney/INDstocks client in Trade repo.
- **Backward compatible:** Keep public names in `dataflows/openalgo.py` as re-exports during migration.
- **Hub SSOT preserved:** Structured research stays in `reports/hub/`; channel is read-through + capture, not a second research store.
- **MCP import safety:** MCP tools may set `TRADE_INTEGRATIONS_SKIP_APPLY=1`; new modules must not require TradingAgents graph patches at import time.
- **Paper first / India hours:** Watch policy must respect existing bridge market-hours gating.
- **No new paid vendors:** nselib stays as optional fallback only when OpenAlgo fails.

---

## Current state (problem map)

| Caller | Path today | Hub channel? | Issue |
|--------|------------|--------------|-------|
| `dataflows/openalgo.py` | `_openalgo_post` (requests) | Partial (`fetch_option_chain`, `fetch_openalgo_quote`) | Duplicate HTTP layer |
| `autonomous_agents/openalgo_client.py` | `OpenAlgoClient._post` | No | Second HTTP layer |
| `nautilus_openalgo_bridge/openalgo_client.py` | extends autonomous_agents | No | Third HTTP layer for quotes |
| MCP `get_options_browse` | hub channel → `_fetch_option_chain_raw` | Yes | Good |
| MCP `get_option_chain` | pip SDK `client.optionchain()` | **No** | Bypasses channel |
| MCP `get_quote` / `get_multi_quotes` | pip SDK direct | **No** | Bypasses channel |
| `identity_in._fetch_openalgo` | `_openalgo_post("quotes")` | **No** | Bypasses channel |
| `chain_openalgo.fetch_chain_stage` | `fetch_option_chain` + nselib | Partial | Fallback scattered |
| Nautilus `OpenAlgoQuoteFeed.poll` | `BridgeOpenAlgoClient.get_multi_quotes` | **No** | Independent poll cadence |

---

## Target architecture

```
All subscribers (TradingAgents, Vibe MCP, Nautilus watch, research stages)
        │
        ▼
hub_capture.channel  —  get_chain / get_quote / get_multi_quotes / get_history
        │                 FreshnessPolicy: LIVE | NORMAL | WATCH
        │                 L1 in-process dedupe (optional, WATCH window)
        ▼
trade_integrations.openalgo.market_data  —  vendor fetch fns + nselib fallback
        │
        ▼
trade_integrations.openalgo.rest_client  —  single _post(), env, retries
        │
        ▼
OpenAlgo REST /api/v1/*  →  broker session (INDmoney / …)
```

**Freshness policies (env-overridable):**

| Policy | Default TTL | Use case |
|--------|-------------|----------|
| `LIVE` | 0 | Agent explicitly asks for fresh chain; research finalize |
| `NORMAL` | `TRADINGAGENTS_OPTIONS_CACHE_MINUTES` (30) | Browse, trade-plan widgets, debate prefetch |
| `WATCH` | `OPENALGO_WATCH_QUOTE_TTL_SECONDS` (5) | Nautilus poll loop, material alert thresholds |

---

## File map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `integrations/trade_integrations/openalgo/__init__.py` | Public exports |
| Create | `integrations/trade_integrations/openalgo/rest_client.py` | Single `_post`, settings, singleton client |
| Create | `integrations/trade_integrations/openalgo/market_data.py` | quotes, multiquotes, history, optionchain, nselib fallback |
| Create | `integrations/trade_integrations/openalgo/symbols.py` | `resolve_openalgo_symbol`, expiry normalize |
| Create | `integrations/trade_integrations/openalgo/freshness.py` | `FreshnessPolicy`, TTL helpers, L1 cache |
| Modify | `integrations/trade_integrations/hub_capture/channel.py` | Policy-aware reads, `get_multi_quotes`, `get_history` |
| Modify | `integrations/trade_integrations/dataflows/openalgo.py` | Thin re-exports (TradingAgents compat) |
| Modify | `integrations/trade_integrations/autonomous_agents/openalgo_client.py` | Delegate `_post` to `rest_client` |
| Modify | `integrations/nautilus_openalgo_bridge/data_feed.py` | Poll via channel `get_multi_quotes(WATCH)` |
| Modify | `openalgo/mcp/mcpserver.py` | Route market-data tools through channel |
| Modify | `integrations/trade_integrations/dataflows/options_research/sources/chain_openalgo.py` | Remove inline nselib; rely on market_data |
| Modify | `integrations/trade_integrations/dataflows/company_research/sources/identity_in.py` | Use `fetch_openalgo_quote` |
| Test | `tests/test_openalgo_rest_client.py` | New |
| Test | `tests/test_hub_capture_channel.py` | Extend policy + multiquote |
| Test | `tests/test_openalgo_market_data.py` | Chain normalize + fallback |

---

### Task 1: Canonical REST client

**Files:**
- Create: `integrations/trade_integrations/openalgo/rest_client.py`
- Create: `integrations/trade_integrations/openalgo/__init__.py`
- Modify: `integrations/trade_integrations/autonomous_agents/openalgo_client.py`
- Test: `tests/test_openalgo_rest_client.py`

**Interfaces:**
- Produces: `OpenAlgoRestClient.post(path, payload, *, timeout=30) -> dict`, `get_rest_client() -> OpenAlgoRestClient`, `openalgo_settings() -> tuple[str, str]`

- [ ] **Step 1: Write the failing test**

```python
def test_rest_client_post_success(monkeypatch):
    from trade_integrations.openalgo.rest_client import OpenAlgoRestClient

    class FakeResp:
        ok = True
        content = b'{"status":"success","data":{"ltp":100}}'
        def json(self):
            return {"status": "success", "data": {"ltp": 100}}

    monkeypatch.setenv("OPENALGO_API_KEY", "test-key")
    monkeypatch.setenv("OPENALGO_HOST", "http://127.0.0.1:5001")
    monkeypatch.setattr(
        "requests.post",
        lambda url, json, timeout: FakeResp(),
    )
    client = OpenAlgoRestClient()
    body = client.post("quotes", {"symbol": "NIFTY", "exchange": "NSE_INDEX"})
    assert body["data"]["ltp"] == 100
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_openalgo_rest_client.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `rest_client.py`**

Move logic from `autonomous_agents/openalgo_client.py` `_post` (retries on 502/503/504, invalid_api_key handling) and from `dataflows/openalgo.py` `_openalgo_settings` into one class. Merge error behavior: bridge raises `RuntimeError`; dataflows layer will map to `NoMarketDataError` in Task 2.

- [ ] **Step 4: Make `OpenAlgoClient._post` delegate**

```python
# autonomous_agents/openalgo_client.py
from trade_integrations.openalgo.rest_client import get_rest_client

def _post(self, path, payload, *, timeout=30):
    return get_rest_client(host=self.host, api_key=self.api_key).post(
        path, payload, timeout=timeout
    )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_openalgo_rest_client.py tests/test_autonomous_agents.py -v -q` (or nearest existing autonomous_agents tests)
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add integrations/trade_integrations/openalgo/ integrations/trade_integrations/autonomous_agents/openalgo_client.py tests/test_openalgo_rest_client.py
git commit -m "refactor(openalgo): add canonical REST client"
```

---

### Task 2: Market-data module + symbol helpers

**Files:**
- Create: `integrations/trade_integrations/openalgo/symbols.py`
- Create: `integrations/trade_integrations/openalgo/market_data.py`
- Modify: `integrations/trade_integrations/dataflows/openalgo.py`
- Test: `tests/test_openalgo_market_data.py`

**Interfaces:**
- Produces: `fetch_quote_raw(symbol) -> dict|None`, `fetch_multi_quotes_raw(requests) -> dict`, `fetch_history_raw(symbol, start, end, interval) -> pd.DataFrame`, `fetch_option_chain_raw(underlying, exchange, *, expiry_date, strike_count) -> dict`, `fetch_option_chain_with_fallback(...) -> dict` (OpenAlgo then nselib)

- [ ] **Step 1: Move `resolve_openalgo_symbol` and aliases to `symbols.py`**

Cut from `dataflows/openalgo.py` lines 37–70 unchanged.

- [ ] **Step 2: Move vendor fetch implementations to `market_data.py`**

Use `get_rest_client().post()` instead of `_openalgo_post`. Keep chain normalization (`_unwrap`, PCR calc) in one function `normalize_option_chain_response(parsed, underlying, expiry)`.

- [ ] **Step 3: Centralize nselib fallback**

Move `_fetch_nselib_chain` / `_nselib_rows_to_chain` from `chain_openalgo.py` into `market_data.py`. Export `fetch_option_chain_with_fallback` used by channel vendor fn.

- [ ] **Step 4: Thin `dataflows/openalgo.py`**

Replace bodies with imports:

```python
from trade_integrations.openalgo.market_data import (
    fetch_history_raw as fetch_openalgo_history,
    fetch_quote_raw as _fetch_live_quote_raw,
    fetch_option_chain_raw as _fetch_option_chain_raw,
    ...
)
from trade_integrations.openalgo.symbols import resolve_openalgo_symbol
```

Keep `get_openalgo_stock_data`, `load_india_ohlcv`, `get_openalgo_indicators` here (TradingAgents string/CSV adapters) but have them call `market_data` internally.

- [ ] **Step 5: Write tests for chain normalization and fallback flag**

```python
def test_normalize_option_chain_adds_pcr():
    from trade_integrations.openalgo.market_data import normalize_option_chain_response
    raw = {"chain": [{"strike": 100, "ce": {"oi": 100}, "pe": {"oi": 200}}]}
    out = normalize_option_chain_response(raw, "NIFTY", "16JUL26")
    assert out["pcr"] == 2.0
    assert out["source"] == "openalgo"
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_openalgo_market_data.py tests/test_hub_capture_channel.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git commit -m "refactor(openalgo): extract market_data and centralize chain normalize"
```

---

### Task 3: Freshness policies + L1 dedupe

**Files:**
- Create: `integrations/trade_integrations/openalgo/freshness.py`
- Modify: `integrations/trade_integrations/hub_capture/channel.py`
- Test: `tests/test_hub_capture_channel.py`

**Interfaces:**
- Produces: `FreshnessPolicy` enum (`LIVE`, `NORMAL`, `WATCH`), `ttl_seconds(policy) -> int`, `L1Cache.get/set(key, value, ttl)`, channel signatures accept `policy: FreshnessPolicy = NORMAL`

- [ ] **Step 1: Add failing tests for WATCH TTL and L1 dedupe**

```python
def test_watch_policy_uses_short_ttl(monkeypatch):
    from trade_integrations.openalgo.freshness import FreshnessPolicy, ttl_seconds
    monkeypatch.setenv("OPENALGO_WATCH_QUOTE_TTL_SECONDS", "5")
    assert ttl_seconds(FreshnessPolicy.WATCH) == 5
    assert ttl_seconds(FreshnessPolicy.LIVE) == 0

def test_l1_dedupe_within_ttl():
    from trade_integrations.openalgo.freshness import L1Cache
    cache = L1Cache()
    cache.set("NIFTY:quotes", {"ltp": 1}, ttl_seconds=5)
    assert cache.get("NIFTY:quotes")["ltp"] == 1
```

- [ ] **Step 2: Implement `freshness.py`**

Thread-safe dict + monotonic timestamps. `NORMAL` reads existing `_options_cache_ttl_minutes()`.

- [ ] **Step 3: Update `get_chain` and `get_quote`**

Add `policy: FreshnessPolicy = NORMAL`. When `policy == LIVE`, skip hub read, always vendor fetch (still write-through if capture enabled). When `WATCH`, use L1 first, then hub if younger than watch TTL, else vendor.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_hub_capture_channel.py -v`
Expected: PASS (existing tests unchanged for default NORMAL)

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(hub-channel): add FreshnessPolicy and L1 dedupe"
```

---

### Task 4: Extend channel for multiquotes and history

**Files:**
- Modify: `integrations/trade_integrations/hub_capture/channel.py`
- Modify: `integrations/trade_integrations/openalgo/market_data.py`
- Test: `tests/test_hub_capture_channel.py`

**Interfaces:**
- Produces: `get_multi_quotes(requests: list[dict], fetch_fn, *, policy=WATCH) -> dict[str, dict]`, `get_history(symbol, start, end, interval, fetch_fn, *, policy=NORMAL) -> pd.DataFrame`

- [ ] **Step 1: Write failing multiquote test**

Mock vendor fetch; two calls within WATCH window → vendor called once.

- [ ] **Step 2: Implement `get_multi_quotes`**

Key L1 cache per `(symbol, exchange)` quote row. For unregistered symbols, pass through to vendor with L1 dedupe only (no hub parquet write unless entity registered).

- [ ] **Step 3: Implement `get_history`**

Hub-first: check if `reports/hub/_data/capture/...` or factor parquet covers range (optional fast path for index research). Default: vendor fetch + optional write-through to capture when entity registered and `should_capture(entity, "history")` (add factor group if needed, or skip write in v1 and only dedupe L1).

**v1 scope:** `get_history` uses L1 dedupe + vendor fetch; hub parquet read for history is optional stretch — document as skip if not needed for Nautilus.

- [ ] **Step 4: Run tests and commit**

```bash
git commit -m "feat(hub-channel): add get_multi_quotes and get_history"
```

---

### Task 5: MCP market-data tools → channel

**Files:**
- Modify: `openalgo/mcp/mcpserver.py`
- Test: `tests/test_mcp_hub_channel_routing.py` (new, mock vendor)

**Interfaces:**
- Consumes: `get_chain`, `get_quote`, `get_multi_quotes`, `fetch_*_raw` from market_data
- Produces: MCP tools behave identically externally but hit channel

- [ ] **Step 1: Write test that patches vendor and counts calls**

Simulate two `get_option_chain` MCP invocations within NORMAL TTL for registered NIFTY → one vendor call.

- [ ] **Step 2: Change `get_option_chain` to use `_chain_snapshot_via_hub_channel`**

Replace direct `client.optionchain(**params)` body with hub channel call (same as browse). Add docstring note: "Uses hub cache when entity registered; pass refresh via env LIVE if needed later."

- [ ] **Step 3: Route `get_quote` through channel**

```python
from trade_integrations.openalgo.market_data import fetch_quote_raw
from trade_integrations.hub_capture.channel import get_quote
quote = get_quote(symbol, fetch_quote_raw, policy=FreshnessPolicy.NORMAL)
```

- [ ] **Step 4: Route `get_multi_quotes` through `get_multi_quotes(..., policy=NORMAL)`**

- [ ] **Step 5: Keep pip SDK `client` for execution tools only** (placeorder, basketorder, funds, positionbook) — add comment block in mcpserver separating "market data → channel" vs "execution → SDK".

- [ ] **Step 6: Run MCP-related tests + smoke**

Run: `pytest tests/test_mcp_hub_channel_routing.py -v`
Run: `python scripts/verify_hub_integration.py` (if OpenAlgo up)

- [ ] **Step 7: Commit**

```bash
git commit -m "fix(mcp): route market-data tools through hub channel"
```

---

### Task 6: Nautilus watch feed → channel

**Files:**
- Modify: `integrations/nautilus_openalgo_bridge/data_feed.py`
- Modify: `integrations/nautilus_openalgo_bridge/openalgo_client.py` (keep quote methods but mark deprecated for market data)
- Test: extend `tests/test_nautilus_data_feed.py` or add `tests/test_nautilus_channel_feed.py`

**Interfaces:**
- Consumes: `get_multi_quotes(..., policy=FreshnessPolicy.WATCH)`
- Produces: `OpenAlgoQuoteFeed.poll()` unchanged signature

- [ ] **Step 1: Write test patching channel**

Assert `OpenAlgoQuoteFeed.poll(["NIFTY"])` calls `get_multi_quotes` not `client.get_multi_quotes`.

- [ ] **Step 2: Refactor `OpenAlgoQuoteFeed.poll`**

```python
from trade_integrations.hub_capture.channel import get_multi_quotes
from trade_integrations.openalgo.market_data import fetch_multi_quotes_raw
from trade_integrations.openalgo.freshness import FreshnessPolicy

rows = get_multi_quotes(
    requests,
    fetch_multi_quotes_raw,
    policy=FreshnessPolicy.WATCH,
)
# map to QuoteSnapshot (existing parse logic)
```

- [ ] **Step 3: Run nautilus bridge tests**

Run: `pytest tests/test_nautilus_channel_feed.py integrations/nautilus_openalgo_bridge/tests/ -v -q`
Run: `python scripts/verify_nautilus_toolchain.py`

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor(nautilus-bridge): poll quotes via hub channel WATCH policy"
```

---

### Task 7: Remove remaining bypass call sites

**Files:**
- Modify: `integrations/trade_integrations/dataflows/company_research/sources/identity_in.py`
- Modify: `integrations/trade_integrations/dataflows/company_research/sources/macro_in.py`
- Modify: `integrations/trade_integrations/dataflows/options_research/sources/chain_openalgo.py`
- Modify: `scripts/verify_india_data_sources.py`

- [ ] **Step 1: `identity_in._fetch_openalgo` → `fetch_openalgo_quote`**

Uses channel when entity registered.

- [ ] **Step 2: `macro_in` live snapshots → `fetch_openalgo_quote` / channel**

- [ ] **Step 3: `chain_openalgo.fetch_chain_stage` → `fetch_option_chain_with_fallback` only**

Delete local nselib helpers.

- [ ] **Step 4: Update verify script to import from `trade_integrations.openalgo.market_data`**

- [ ] **Step 5: Grep guard — no direct `_openalgo_post` outside openalgo package**

Run: `rg '_openalgo_post|client\.optionchain|client\.quotes' integrations/ openalgo/mcp/ vibetrading/ --glob '*.py'`
Expected: hits only inside `trade_integrations/openalgo/` and execution SDK paths.

- [ ] **Step 6: Commit**

```bash
git commit -m "refactor: remove direct OpenAlgo bypasses from research sources"
```

---

### Task 8: Env, docs in skill, verification

**Files:**
- Modify: `.env.example` — add `OPENALGO_WATCH_QUOTE_TTL_SECONDS=5`
- Modify: `stack/vibe/skills/trade-stack/SKILL.md` — document single channel rule
- Modify: `integrations/trade_integrations/env.py` — optional helper for watch TTL

- [ ] **Step 1: Document env vars in `.env.example`**

- [ ] **Step 2: Update trade-stack skill hub section** — "All market-data reads go through `hub_capture.channel` with FreshnessPolicy; never call OpenAlgo REST or SDK for quotes/chain/history outside `trade_integrations/openalgo/`."

- [ ] **Step 3: Full verification suite**

Run:
```bash
pytest tests/test_openalgo_rest_client.py tests/test_openalgo_market_data.py tests/test_hub_capture_channel.py tests/test_mcp_hub_channel_routing.py -v
python scripts/verify_hub_integration.py
python scripts/verify_india_data_sources.py RELIANCE
```

- [ ] **Step 4: Commit**

```bash
git commit -m "docs: unified openalgo channel env and skill notes"
```

---

### Task 9 (deferred): WebSocket feed for Nautilus — separate plan

**Not in Phase 1 scope.** Track as follow-up issue / amend `docs/superpowers/plans/2026-07-16-nautilus-openalgo-bridge.md`.

**Approach when ready:**
- Add `integrations/trade_integrations/openalgo/ws_client.py` subscribing to OpenAlgo WebSocket proxy (`WEBSOCKET_PORT`, default 8765).
- Publish ticks into same L1 cache that WATCH policy reads → Nautilus gets sub-second data without extra REST polls.
- Requires: auth handshake per OpenAlgo WS docs, symbol subscription map shared with bridge `instruments.py`.

**Exit criteria to start Task 9:** REST+channel WATCH policy proven in production; watch latency still insufficient for sub-second rules.

---

## Migration / rollout

1. **Phase 1 (Tasks 1–8):** Safe to ship incrementally; each task keeps tests green.
2. **Feature flag (optional):** `OPENALGO_CHANNEL_ENFORCE=1` — when unset, log warning on bypass (Task 7 grep becomes CI check).
3. **No user-visible behavior change** for browse/trade-plan (already channel-backed); `get_option_chain` MCP becomes cache-aware (may return fresher hub data within TTL — document in tool docstring).
4. **Nautilus:** Fewer duplicate REST calls to OpenAlgo when Vibe browse and watch run concurrently (L1 dedupe).

---

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| MCP `get_option_chain` suddenly cached | Use NORMAL policy; agent can set env `TRADINGAGENTS_OPTIONS_CACHE_MINUTES=0` for LIVE-like behavior; long-term add `refresh=true` param |
| Circular import MCP ↔ trade_integrations | Keep `_ensure_trade_stack_import()` lazy imports in mcpserver |
| TradingAgents stale OHLCV tests | `load_india_ohlcv` keeps yfinance fallback; history policy NORMAL with 0 TTL for debate runs via env |
| nselib fallback hidden behind channel | Always set `vendor` field on chain payload; log when fallback used |
| Bridge order paths break | Task 6 only changes `data_feed.py`; execute.py untouched |

---

## Success criteria

- [ ] One `rest_client.post` implementation used by autonomous_agents, bridge, and market_data
- [ ] Zero `_openalgo_post` outside `trade_integrations/openalgo/`
- [ ] MCP `get_option_chain`, `get_quote`, `get_multi_quotes`, `get_options_browse` all use hub channel
- [ ] Nautilus `OpenAlgoQuoteFeed` uses channel WATCH policy
- [ ] nselib fallback exists only in `market_data.py`
- [ ] `pytest` + `verify_hub_integration.py` pass
- [ ] `channel_stats_today()` shows increased `hub_hits` / reduced `vendor_fetches` under concurrent load

---

## Self-review (spec coverage)

| Requirement | Task |
|-------------|------|
| Single REST client | Task 1 |
| Single market-data module | Task 2 |
| Hub channel for all subscribers | Tasks 3–6 |
| Consistent cache/freshness | Task 3 |
| MCP alignment | Task 5 |
| Nautilus alignment | Task 6 |
| nselib centralized | Task 2, 7 |
| WebSocket (future) | Task 9 deferred |
| Tests | All tasks |
| No direct INDmoney | Global constraints |
