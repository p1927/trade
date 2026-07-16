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

- `start.sh`, `Makefile`, `trade` — run the stack (default: **Vibe Web UI** at http://localhost:5899)
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
./start.sh

# Or use the unified CLI
./trade start openalgo          # OpenAlgo only
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
├── scripts/           # sync, setup_vibe.py, ensure_vibe_frontend.sh
├── trade              # unified CLI (stack, tunnel, webhooks, research)
├── start.sh           # default → Vibe Web UI at http://localhost:5899
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

```bash
./start.sh                  # default → Vibe Web UI at http://localhost:5899
./start.sh --status         # check SearXNG, OpenAlgo, Vibe, MCP readiness
./start.sh --openalgo-only  # OpenAlgo only (no Vibe UI)
```

| Service | URL | Role |
|---------|-----|------|
| Vibe Web UI | http://localhost:5899 | Chat, research plans, strategy review |
| Vibe API | http://localhost:8899 | Backend for the UI |
| OpenAlgo | http://127.0.0.1:5001 | Broker bridge, option chain, execution |

**Autonomous agents:** Nautilus watch is **on by default** (`NAUTILUS_WATCH_ENABLE=true`). Opt out with `NAUTILUS_WATCH_ENABLE=0`. Hub UI at `/autonomous` shows scheduler + Nautilus health, mandate chips, and last decision per agent.

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Frontend dependencies not installed` | `./scripts/ensure_vibe_frontend.sh` |
| `Skipped MCP server 'openalgo': Connection closed` | `python scripts/setup_vibe.py --verify` then reinstall openalgo venv deps |
| `ModuleNotFoundError: No module named 'pandas'` in MCP logs | Use `scripts/run_openalgo_mcp.sh` (regenerate via `setup_vibe.py`) |
| UI proxy errors on first load | Backend starts after frontend — refresh after ~5s |
| Agent can't fetch live options | Ensure OpenAlgo is running and `OPENALGO_API_KEY` is set in `.env` |
| Agent only chats, no structured plan | Run `python scripts/setup_vibe.py --force-env` then ask with options-advisor skill; MCP tools `get_options_browse` / `get_options_trade_plan` |
| Analytics shows fallback IV regime | `pip install -e '.[stack,options]'` for qfinindia, then `python scripts/run_options_research.py NIFTY` |

Edit code under `vibetrading/` directly; commit in the fork repo, then bump the submodule pointer in `trade`.

Alpaca DO NOT remove 
a7bd963e-1df9-4ad3-8a83-bd351780cc48
999pratyush@gmail.com
Hellomotorola@123