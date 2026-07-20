# Trade Stack

Unified wrapper around submodules for research, chat, and Indian market execution:

| Path | Source | Role |
|------|--------|------|
| `tradingagents/` | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | Batch multi-agent research (`./start.sh --cli`) |
| `openalgo/` | [marketcalls/openalgo](https://github.com/marketcalls/openalgo) | Broker bridge, web UI, MCP execution |
| `vibetrading/` | [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) | Chat Web UI, plans, OpenAlgo MCP client |
| `nautilus_trader/` | [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | Watch/state/risk engine (OpenAlgo feed + execution intents) |

Forks live under [p1927](https://github.com/p1927) (`p1927/TradingAgents`, `p1927/openalgo`, `p1927/Vibe-Trading`) so you can patch and sync upstream like the other submodules.

Trade-level orchestration:

- **`./trade`** — single CLI for stack lifecycle, tunnels, webhooks, research
- `start.sh` — foreground modes only (interactive Vibe dev, OpenAlgo-only, TradingAgents CLI)
- `docker-compose.stack.yml`, `stack/` — SearXNG + Vibe operator templates
- `integrations/` — OpenAlgo data, news, company research, agent patches, `nautilus_openalgo_bridge/`
- `reports/hub/` — shared research dossiers (TradingAgents + Vibe)
- `exposure/` — Cloudflare Tunnel + webhook exposure (TradingView, GoCharting, Chartink)
- `scripts/setup_vibe.py` — wire OpenAlgo MCP into `~/.vibe-trading/agent.json`

## Clone

```bash
git clone --recurse-submodules https://github.com/p1927/trade.git
cd trade
cp .env.example .env   # configure keys

# First-time: verify dependencies, then start the background stack
./trade doctor
./trade up

# Or foreground dev (Vite HMR + API reload)
./trade dev
```

Other entry points:

```bash
./trade start openalgo          # OpenAlgo only (webhook setup)
./trade tunnel quick            # expose webhooks via Cloudflare
./trade webhooks tradingview    # show URL + where to paste it in TradingView
```

## Sync upstream

```bash
make sync-status   # see what's new upstream
make sync          # merge upstream into all submodules
./scripts/sync.sh vibetrading   # Vibe only
```

## Layout

```
trade/
├── tradingagents/     # submodule — AI research engine (CLI via ./start.sh --cli)
├── openalgo/          # submodule — broker bridge + UI + MCP server
├── vibetrading/       # submodule — Vibe chat UI + agent (editable install)
├── nautilus_trader/   # submodule — NautilusTrader source (watch node; PyPI wheel at runtime)
├── integrations/      # trade_integrations + nautilus_openalgo_bridge
├── stack/vibe/        # Vibe agent.json template + trade-stack skill
├── reports/hub/       # shared company research dossiers
├── exposure/          # Cloudflare tunnel + webhook platform URLs
├── scripts/           # sync, setup_vibe.py, stack_ctl (via ./trade), ensure_vibe_frontend.sh
├── trade              # unified CLI — stack lifecycle, tunnel, webhooks, research
├── start.sh           # foreground modes (dev UI, OpenAlgo-only, TradingAgents CLI)
└── Makefile
```

## Vibe Trading (local fork)

The chat UI runs from the **vibetrading/** submodule, not PyPI. It connects to OpenAlgo for live Indian options data and order execution via MCP.

### Prerequisites

- Node.js 20+ (for the Web UI frontend)
- OpenAlgo running with broker connected and API key in root `.env`
- OpenAlgo Python venv with MCP dependencies (`openalgo/.venv`)

### First-time setup

```bash
git submodule update --init vibetrading openalgo

# Trade stack venv + Vibe agent
pip install -e vibetrading/          # or ./start.sh (bootstrap does this)

# Vibe Web UI frontend
./scripts/ensure_vibe_frontend.sh    # npm install in vibetrading/frontend

# OpenAlgo MCP bridge (required for options chain + orders in chat)
cd openalgo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..

# Wire OpenAlgo MCP + trade-stack skills into ~/.vibe-trading/
python scripts/setup_vibe.py --force-env
python scripts/setup_vibe.py --verify   # should print: OpenAlgo MCP: ok
```

### Run

Use **`./trade`** for the background stack (OpenAlgo + Vibe API + Vite UI + hub Docker). Each service registers a **PID claim** under `log/claims/` so start/stop never stomp foreign processes.

```bash
./trade doctor              # preflight — run before first start or after .env changes
./trade doctor --hub        # hub Docker probes only (SearXNG, Redis, Timescale)
./trade up                  # start/heal background stack (recommended)
./trade status              # read-only health summary (does not start services)
./trade status --json       # machine-readable dependency matrix
./trade heal                # start missing services
./trade heal --hub-only     # hub Docker tier only (SearXNG, Redis, Timescale)
./trade restart             # heal — start only what's down
./trade restart --force     # full stop + preflight + start
./trade down                # stop stack + watchers; clear pidfiles and claims
./trade down --all          # full teardown — docker compose down + tunnels + cleanup
./trade down --hub          # stop hub Docker only (app tier keeps running)
./trade dev                 # dev mode: auto-reload API/UI (blocks if hub deps down)
./trade reload app          # restart app tier after code edits (if not using trade dev)
```

Foreground / special modes (`start.sh`):

```bash
./start.sh --openalgo-only  # OpenAlgo only (no Vibe UI)
./start.sh --cli            # TradingAgents research CLI
./start.sh --status         # extended bootstrap status (TradingAgents, hub tier)
```

| Service | URL | Role |
|---------|-----|------|
| Vibe Web UI | http://localhost:5899 | Chat, research plans, strategy review |
| Vibe API | http://localhost:8899 | Backend for the UI |
| OpenAlgo | http://127.0.0.1:5001 | Broker bridge, option chain, execution |

**PID claims:** `log/claims/{openalgo,vibe-api,vibe-ui,nautilus-watch}.claim` — written on start, removed on `trade down`. Session stamp: `log/stack.session`. Summary: `log/stack.instance`. If a port is held by an unclaimed process, `trade up` fails fast with a clear message instead of killing it.

**Hub Docker env flags** (see `stack/ports.yaml`):

| Variable | Default | Role |
|----------|---------|------|
| `STACK_START_SEARXNG` | `1` | Start/probe SearXNG on `:5556` |
| `TIMESCALE_ENABLED` | off | Enable TimescaleDB hot ticks |
| `STACK_START_TIMESCALE` | `1` | Start Timescale when enabled |
| `NAUTILUS_WATCH_ENABLE` | `1` | Requires Redis when on |
| `STACK_CLEAN_HUB_ON_BOOT` | `0` | Force-recreate hub containers on `trade up` |
| `STACK_HEAL_DAEMON` | `1` | Background `trade heal` every 60s (daemon mode) |

**Autonomous agents:** Hub UI at `/autonomous` — create agents in chat, confirm proposals, then bootstrap runs immediately (watch summary + first research turn in agent chat). Cards show `initializing` → `scheduler ok`, Nautilus `poll` / `expected` / `node_on`, and `watch ready` vs `position tracked`.

| Setting | Default | Role |
|---------|---------|------|
| `NAUTILUS_WATCH_ENABLE` | `true` | India watch bridge (opt out with `0`) |
| `AUTONOMOUS_AGENTS_ENABLE_SCHEDULER` | `1` | Per-agent watch + research jobs |
| `VIBE_TRADING_ENABLE_SCHEDULER` | `1` | Vibe API job executor (required for bootstrap resume) |

**Nautilus watch process** (continuous alerts between scheduler ticks):

```bash
# After confirming an India agent (aa_… id shown in hub or commit toast):
trade start nautilus-watch --agent-id aa_your_agent_id

# Or set in .env and restart stack:
# NAUTILUS_AGENT_ID=aa_your_agent_id

# Background stack (OpenAlgo + Vibe) also auto-starts watch when a running India agent exists:
trade up
```

If `.venv-nautilus` is missing, the stack falls back to the **legacy poll loop** (same OpenAlgo feed; run `./scripts/setup_nautilus.sh` for full TradingNode). Verify: `./scripts/verify_autonomous_integration.py`

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Frontend dependencies not installed` | `./scripts/ensure_vibe_frontend.sh` |
| `Skipped MCP server 'openalgo': Connection closed` | `python scripts/setup_vibe.py --verify` then reinstall openalgo venv deps |
| `ModuleNotFoundError: No module named 'pandas'` in MCP logs | Use `scripts/run_openalgo_mcp.sh` (regenerate via `setup_vibe.py`) |
| UI proxy errors on first load | Backend starts after frontend — refresh after ~5s |
| `cannot start … :port held by foreign pid` | Stop the other process, or `trade down` then `trade up` |
| Stale lock at `log/.stack.lock.d` | No `trade` command running — `rm -rf log/.stack.lock.d` |
| Agent can't fetch live options | Ensure OpenAlgo is running and `OPENALGO_API_KEY` is set in `.env` |
| Agent only chats, no structured plan | Run `python scripts/setup_vibe.py --force-env` then ask with options-advisor skill; MCP tools `get_options_browse` / `get_options_trade_plan` |
| Analytics shows fallback IV regime | `pip install -e '.[stack,options]'` for qfinindia, then `python scripts/run_options_research.py NIFTY` |

Edit code under `vibetrading/` directly; commit in the fork repo, then bump the submodule pointer in `trade`.

Alpaca DO NOT remove 
a7bd963e-1df9-4ad3-8a83-bd351780cc48
999pratyush@gmail.com
Hellomotorola@123