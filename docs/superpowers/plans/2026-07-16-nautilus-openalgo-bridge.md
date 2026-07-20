# NautilusTrader ↔ OpenAlgo Bridge Implementation Plan

> **Status (2026-07-20):** **v1 SHIPPED** — `integrations/nautilus_openalgo_bridge/runtime/poll_loop.py` is the production watch path (OpenAlgo quotes → rule eval → Vibe trigger → EXIT intents). TradingNode/WatchActor integration remains **v2 backlog**; do not block autonomous agents on full Nautilus node boot.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add NautilusTrader as the watch/state/risk maintainer; OpenAlgo remains the sole India data source and order executor; Vibe agents decide strategy on Nautilus alerts.

**Architecture:** OpenAlgo feeds live quotes/chain into Nautilus via a custom DataClient. Nautilus WatchActors evaluate watch rules, persist portfolio state (optional Redis), and emit signals. On material alerts, Nautilus triggers a Vibe autonomous session turn. When Nautilus finalizes an action (ENTER/ADJUST/EXIT/HOLD), an **ExecutionIntent** is sent to OpenAlgo REST/MCP — Nautilus never talks to the broker directly.

**Tech Stack:** NautilusTrader (LGPL-3.0, Python ≥3.12), OpenAlgo REST `/api/v1/*`, Vibe API (`/sessions/{id}/messages`), existing `trade_integrations/autonomous_agents`, optional Redis.

## Global Constraints

- **Execution authority:** OpenAlgo only. Nautilus produces structured intents; bridge translates to `basketorder` / `closeposition` / `optionsmultiorder`.
- **India market:** No native NSE adapter in Nautilus; all symbology and F&O charges stay in OpenAlgo.
- **Mind:** Vibe + TradingAgents hub for strategy; Nautilus does not call LLMs directly in v1 (HTTP trigger to Vibe only).
- **Paper first:** Analyzer/sandbox mode until explicit live flag.
- **Market hours:** India session gating via existing `live/runtime/triggers.py` patterns.
- **Single order reconciliation source:** OpenAlgo `positionbook` wins on conflict with Nautilus cache.
- **Submodule:** `nautilus_trader/` tracks [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) `develop` branch (read-only upstream; fork to p1927 later if patches needed).

---

## Locked roles

| Component | Role |
|-----------|------|
| **TradingAgents + hub** | Batch research, ranked strategies, prediction dossiers |
| **Vibe agent** | Strategy mind — pick plan, confidence, watch rules; act on full turns |
| **NautilusTrader** | Watch engine, timers, signals, portfolio cache, risk gates, multi-week state |
| **OpenAlgo** | Live India data + broker execution + MCP tools |
| **`integrations/nautilus_openalgo_bridge/`** | Data feed, intent transport, Vibe trigger, handoff |

---

## Data flow (target)

```
OpenAlgo (quotes, chain, positions)
        │ REST / WebSocket feed
        ▼
nautilus_openalgo_bridge/data_feed.py  →  Nautilus DataEngine
        │
        ▼
WatchActor(s) — evaluate watch_rules, thesis thresholds
        │ signal: REVIEW_NEEDED | EXIT_NOW | HOLD
        ├──────────────────────────────┐
        ▼                              ▼
Vibe API (full reasoning turn)    ExecutionIntent queue
        │                              │
        ▼                              ▼
Updated thesis / plan JSON      openalgo_bridge/execute.py
                                       │
                                       ▼
                                OpenAlgo REST basket/close
```

---

## File map (to create)

```
trade/
├── nautilus_trader/                          # submodule (upstream)
├── integrations/
│   └── nautilus_openalgo_bridge/
│       ├── __init__.py
│       ├── config.py                         # env: hosts, symbols, redis
│       ├── models.py                         # ExecutionIntent, WatchSpec, Handoff
│       ├── openalgo_client.py                # reuse/wrap auto_paper client
│       ├── data_feed.py                      # OpenAlgo → QuoteTick/Bar/custom data
│       ├── instruments.py                    # NSE/NFO InstrumentProvider
│       ├── watch_actor.py                    # Nautilus Actor: rules + signals
│       ├── risk_actor.py                     # max loss, flatten-at-close (Phase 4)
│       ├── vibe_trigger.py                   # POST message to Vibe session
│       ├── execute.py                        # Intent → OpenAlgo basket/close
│       ├── handoff.py                        # Vibe fill → Nautilus state
│       ├── node.py                           # TradingNode bootstrap
│       └── runtime/
│           └── run_watch_node.py             # CLI entrypoint
├── scripts/
│   ├── run_nautilus_watch.sh                 # start watch node
│   └── setup_nautilus.sh                     # pip install nautilus_trader, verify
└── docs/superpowers/plans/                   # this file
```

---

## Phase 0 — Submodule & toolchain (done / verify)

### Task 0: Submodule and docs

**Files:**
- `.gitmodules` — `nautilus_trader` entry
- `README.md` — stack table + layout
- `scripts/sync.sh` — `nautilus` target
- `.env.example` — Nautilus env block
- `pyproject.toml` — optional `[nautilus]` extra

- [x] Add `nautilus_trader` submodule (`develop`, shallow)
- [ ] Run `git submodule update --init nautilus_trader`
- [ ] Document Python **3.12+** requirement for Nautilus node (separate venv recommended: `.venv-nautilus/`)

**Setup script `scripts/setup_nautilus.sh`:**
- [ ] Create `.venv-nautilus` with Python 3.12+
- [ ] `pip install nautilus_trader` (PyPI wheel) OR `pip install -e nautilus_trader/` if building from source
- [ ] `pip install redis` if persistence enabled
- [ ] Smoke: `python -c "import nautilus_trader; print(nautilus_trader.__version__)"`

**Sync `scripts/sync.sh`:**
- [ ] Add `sync_nautilus()` — fetch upstream `develop`, fast-forward submodule pointer (no p1927 fork yet)
- [ ] Add `nautilus|nt` case target
- [ ] Include in `show_status`

---

## Phase 1 — Models & config (bridge skeleton)

### Task 1: Core types

**Files:** `integrations/nautilus_openalgo_bridge/models.py`, `config.py`

- [ ] Define `WatchRule` — `{symbol, metric, threshold, direction}` (spot_move_pct, level_above, vix, oi_change)
- [ ] Define `WatchSpec` — list of rules + `gate.skip_if_unchanged_minutes`
- [ ] Define `ExecutionIntent` — `{action: ENTER|ADJUST|EXIT|HOLD, legs[], strategy, agent_id, widget_id, rationale, confidence}`
- [ ] Define `PositionHandoff` — Vibe/OpenAlgo fill → Nautilus tracked state
- [ ] Define `BridgeConfig` from env (`NAUTILUS_*`, reuse `OPENALGO_*`)

**Verify:** unit test `tests/test_nautilus_bridge_models.py` — serialize/deserialize JSON roundtrip

---

## Phase 2 — OpenAlgo data feed (entry bridge)

### Task 2: Instrument mapping

**Files:** `instruments.py`

- [ ] Map NIFTY, BANKNIFTY, FINNIFTY, INDIAVIX index symbols to Nautilus `InstrumentId` (venue `NSE` / `NFO`)
- [ ] Load option legs via OpenAlgo `symbolinfo` / `optionchain` when handoff includes strikes
- [ ] Cache instrument definitions in Nautilus `InstrumentProvider`

### Task 3: DataClient / feed poller

**Files:** `data_feed.py`, wrap `openalgo_client.py`

- [ ] Poll `multiquotes` on interval (config `NAUTILUS_QUOTE_POLL_MS`, default 2000) for watch symbols
- [ ] Emit `QuoteTick` or aggregate 1-min `Bar` into Nautilus data engine
- [ ] Optional: subscribe OpenAlgo WebSocket (`/api/websocket/subscribe`) for lower latency
- [ ] Publish custom data type `IndiaVixSnapshot` / `OptionChainSummary` if needed for rules

**Reference:** Nautilus `adapters/_template/data.py`, `docs/developer_guide/adapters.md`

**Verify:** run feed standalone; log tick count for NIFTY over 60s with OpenAlgo up

---

## Phase 3 — WatchActor (Nautilus maintainer core)

### Task 4: Watch actor

**Files:** `watch_actor.py`, `node.py`

- [ ] Nautilus `Actor` subscribing to index quote streams
- [ ] Load `WatchSpec` from autonomous agent instance JSON (`reports/hub/_data/autonomous_agents/{id}.json`) or handoff file
- [ ] Evaluate rules each tick/bar; debounce duplicate alerts (`NAUTILUS_ALERT_COOLDOWN_SEC`)
- [ ] `publish_signal("REVIEW_NEEDED", payload)` / `EXIT_NOW` / `THESIS_BROKEN`
- [ ] `clock.set_timer` for periodic heartbeat + flatten-at-close (IST 15:10)
- [ ] `on_stop` cancel timers

**Reference:** `nautilus_trader/examples/backtest/example_11_messaging_with_actor_signals`

**Verify:** backtest mode with recorded quote CSV; assert signal fires when spot move > threshold

### Task 5: TradingNode bootstrap

**Files:** `node.py`, `scripts/run_nautilus_watch.sh`

- [ ] Minimal `TradingNodeConfig` — OpenAlgo feed only, **no Nautilus execution client**
- [ ] Register `WatchActor` + optional `RiskActor` stub
- [ ] Optional `RedisCacheConfig` when `NAUTILUS_REDIS_URL` set
- [ ] CLI: `python -m nautilus_openalgo_bridge.runtime.run_watch_node --agent-id aa_xxx`

**Verify:** `./scripts/run_nautilus_watch.sh` runs alongside OpenAlgo; status log every minute

---

## Phase 4 — Vibe trigger (mind callback)

### Task 6: Alert → Vibe full turn

**Files:** `vibe_trigger.py`, integrate with `autonomous_agents/watch.py`

- [ ] On `REVIEW_NEEDED` signal → `POST {VIBE_BACKEND_URL}/sessions/{vibe_session_id}/messages` with alert payload + Nautilus cache snapshot (spot, positions summary)
- [ ] Reuse `dispatch_full_reasoning` prompt builder from `autonomous_agents/turns.py`
- [ ] Dedupe if Vibe session `streaming=true`
- [ ] Write watch summary to agent chat (existing SSE path)

**Verify:** fire synthetic alert; confirm Vibe session receives message and starts turn

---

## Phase 5 — ExecutionIntent → OpenAlgo (exit path first)

### Task 7: Intent executor (OpenAlgo only)

**Files:** `execute.py`

- [ ] Subscribe to Nautilus signal `EXECUTE_INTENT` or read from intent queue file/Redis
- [ ] Map `ExecutionIntent`:
  - `EXIT` → OpenAlgo `closeposition` or leg-wise sells via `basketorder`
  - `ENTER` / `ADJUST` → delegate to existing `execute_auto_paper_basket` MCP pattern (legs JSON)
  - `HOLD` → log only via `record_autonomous_decision`
- [ ] Pre-flight: `calculate_margin`, analyzer mode, market hours
- [ ] Post-flight: reconcile `positionbook` → update agent `thesis` + Nautilus cache handoff

**Rule:** Nautilus **never** implements `LiveExecutionClient` to broker in v1 — only `execute.py` calls OpenAlgo.

**Verify:** paper EXIT on test position; positionbook empty; decision logged

---

## Phase 6 — Handoff & autonomous integration

### Task 8: Vibe entry → Nautilus state

**Files:** `handoff.py`, hook in `auto_paper/mcp_actions.py` or autonomous `record_autonomous_decision`

- [ ] After successful Vibe/OpenAlgo entry basket → write `PositionHandoff` JSON:
  `{agent_id, widget_id, legs, entry_spot, stop_rules, watch_spec, created_at}`
- [ ] Nautilus WatchActor reloads handoff on start and on file change
- [ ] Merge with autonomous agent instance `thesis` + `watch_rules`

### Task 9: Wire autonomous agents

**Files:** `autonomous_agents/turns.py`, `proposals.py`

- [ ] On full reasoning turn, agent outputs `watch_spec` in structured block (or MCP tool `set_agent_watch_spec`)
- [ ] Persist on agent instance JSON
- [ ] Nautilus node watches `watch_spec` path or Redis key

**Verify:** end-to-end paper session — create agent → Vibe picks straddle → handoff → Nautilus watches → synthetic alert → Vibe re-turn → EXIT intent → OpenAlgo close

---

## Phase 7 — Risk & persistence (multi-week)

### Task 10: RiskActor

**Files:** `risk_actor.py`

- [ ] Hard gates (no LLM): `max_daily_loss_inr`, `max_open_positions`, market hours, duplicate intent dedupe
- [ ] Subscribe to Nautilus portfolio/account updates fed from OpenAlgo periodic `funds` + `positionbook` poll
- [ ] `publish_signal("HALT_TRADING")` on breach

### Task 11: Redis persistence

- [ ] Enable `RedisCacheConfig` in `node.py` when `NAUTILUS_REDIS_URL` set
- [ ] Stable `instance_id` across restarts (`NAUTILUS_INSTANCE_ID`)
- [ ] Document flush policy: `flush_on_start=false`

**Reference:** [Nautilus cache persistence](https://nautilustrader.io/docs/latest/concepts/cache/)

---

## Phase 8 — Stack integration

### Task 12: CLI & start.sh

**Files:** `trade`, `start.sh`, `scripts/stack_lib.sh`

- [ ] `trade start nautilus-watch` — OpenAlgo + Nautilus watch node
- [ ] `trade status` — show Nautilus watch node PID / health
- [ ] `NAUTILUS_WATCH_ENABLE=1` gate in `.env`

### Task 13: Env & optional deps

**Files:** `.env.example`, `pyproject.toml`

```bash
# Nautilus watch node (OpenAlgo feed → alerts → Vibe; execution via OpenAlgo)
#NAUTILUS_WATCH_ENABLE=false
#NAUTILUS_QUOTE_POLL_MS=2000
#NAUTILUS_WATCH_SYMBOLS=NIFTY,BANKNIFTY,INDIAVIX
#NAUTILUS_ALERT_COOLDOWN_SEC=300
#NAUTILUS_REDIS_URL=redis://127.0.0.1:6379/0
#NAUTILUS_INSTANCE_ID=trade-watch-1
#NAUTILUS_PYTHON=python3.12   # for .venv-nautilus
```

- [ ] `[project.optional-dependencies] nautilus = ["nautilus_trader>=1.228", "redis>=5"]`

---

## Testing strategy

| Phase | Test |
|-------|------|
| 1 | Model JSON roundtrip |
| 2 | Feed poll returns NIFTY LTP |
| 3 | Backtest signal on synthetic bars |
| 4 | Vibe message injected on alert |
| 5 | Paper EXIT via intent |
| 6 | Full autonomous agent E2E (manual, market hours) |

Mark integration tests `@pytest.mark.integration` — require OpenAlgo + Vibe running.

---

## Order authority (critical)

```
ENTERS (multi-leg, charges, widget)  →  Vibe agent → OpenAlgo MCP basket
EXITS / STOPS / FLATTEN / ROLLS      →  Nautilus intent → execute.py → OpenAlgo
RECONCILIATION                       →  OpenAlgo positionbook (source of truth)
```

Never enable parallel Nautilus `LiveExecutionClient` to broker until Phase 9+ and explicit review.

---

## Future (out of scope for v1)

- Full Nautilus `LiveExecutionClient` adapter (only if sub-minute native order state required)
- Backtest watch rules on historical OpenAlgo parquet catalog
- `nautilus_agents` DecisionPipeline tiers wired to Vibe
- p1927 fork of nautilus_trader for India-specific patches

---

## Suggested implementation order

1. Phase 0 verify + setup script
2. Phase 1 models
3. Phase 2 feed
4. Phase 3 watch actor
5. Phase 4 vibe trigger
6. Phase 5 exit executor
7. Phase 6 handoff + autonomous
8. Phase 7–8 hardening + CLI

**First milestone (M1):** Nautilus watch node runs, polls OpenAlgo, fires Vibe message on NIFTY ±0.5% move — no execution changes.

**Second milestone (M2):** EXIT intent from Nautilus closes paper position via OpenAlgo.

**Third milestone (M3):** Full autonomous agent loop with handoff after Vibe entry.
