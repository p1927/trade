# Phase 5: WebSocket Watch Feed — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Depends on [Phase 3](./2026-07-23-market-authority-phase-3-trading-port.md). **Optional performance phase.**

**Goal:** Nautilus watch hot path uses OpenAlgo WebSocket/ZMQ feed instead of REST poll loop, with MarketContext stamping — sub-100ms tick delivery without bypassing authority.

**Architecture:** New `OpenAlgoWatchFeed` adapter in bridge implementing `TradingConnectorPort.watch_feed()`. Subscribes via OpenAlgo WS proxy (port 8765). Ticks tagged with `context_generation` from handoff. Fallback to REST poll on disconnect.

**Tech Stack:** OpenAlgo websocket_proxy, Python asyncio or threading, existing `data_feed.py`.

## Global Constraints

- No direct broker WebSocket credentials in Nautilus.
- On context_generation mismatch → disconnect feed, reload context, resubscribe.
- REST poll loop remains as `--legacy-poll` fallback (already exists).

---

### Task 1: Watch feed port extension

**Files:**
- Modify: `integrations/trade_integrations/execution/trading_port.py`
- Create: `integrations/trade_integrations/execution/watch_feed.py`
- Test: `tests/test_watch_feed.py`

**Produces:**

```python
@dataclass
class WatchTick:
    symbol: str
    ltp: float
    ts: str
    context_generation: str

class WatchFeedHandle(Protocol):
    def subscribe(self, symbols: list[str]) -> None: ...
    def poll_ticks(self) -> list[WatchTick]: ...
    def close(self) -> None: ...
```

- [ ] **Step 1:** Protocol + in-memory mock feed for tests
- [ ] **Step 2:** Convergence gate
- [ ] **Step 3:** Commit: `feat(execution): WatchFeed port types`

---

### Task 2: OpenAlgo WebSocket client adapter

**Files:**
- Create: `integrations/nautilus_openalgo_bridge/ws_feed.py`
- Test: `tests/test_nautilus_ws_feed.py`

- [ ] **Step 1:** Connect to `ws://127.0.0.1:8765` (configurable), subscribe symbols
- [ ] **Step 2:** Normalize tick format to `WatchTick`
- [ ] **Step 3:** Reconnect with exponential backoff
- [ ] **Step 4:** Convergence gate (FD leak audit per openalgo CLAUDE.md)
- [ ] **Step 5:** Commit: `feat(bridge): OpenAlgo WebSocket watch feed`

---

### Task 3: Integrate poll_loop

**Files:**
- Modify: `integrations/nautilus_openalgo_bridge/runtime/poll_loop.py`
- Modify: `integrations/nautilus_openalgo_bridge/config.py` — `WATCH_FEED_MODE=ws|rest`
- Test: `tests/test_stock_simulator_hf_replay.py` or bridge dry-run tests

- [ ] **Step 1:** When `WATCH_FEED_MODE=ws`, use ws_feed; else existing OpenAlgoQuoteFeed
- [ ] **Step 2:** Pass `context_generation` from handoff into tick handler
- [ ] **Step 3:** Convergence gate
- [ ] **Step 4:** Commit: `feat(bridge): configurable WS watch feed in poll loop`

---

### Task 4: Latency benchmark (non-CI)

**Files:**
- Create: `scripts/bench_watch_feed_latency.py` (dev-only, not pytest gate)

- [ ] **Step 1:** Script compares p50/p95 REST vs WS for 100 ticks
- [ ] **Step 2:** Document results in `.superpowers/sdd/progress.md` (not new summary doc)
- [ ] **Step 3:** Target: WS p95 < 100ms local loopback

---

## Phase 5 verification

```bash
pytest tests/test_watch_feed.py tests/test_nautilus_ws_feed.py tests/test_nautilus_preflight.py -q --timeout=120
WATCH_FEED_MODE=ws trade reload app  # after stack up
# manual: watch agent with spot_move rule, confirm eval latency in bridge logs
```

**Phase completion:** WS mode works with fallback + no FD leaks in audit + Bugbot clean.

## When to skip

If REST poll meets watch SLA (<5s rules on 5–10s poll interval), Phase 5 can defer. Phase 0–3 deliver the architectural win.
