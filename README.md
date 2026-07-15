# Trade Stack

Unified wrapper around submodules for research, chat, and Indian market execution:

| Path | Source | Role |
|------|--------|------|
| `tradingagents/` | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | Batch multi-agent research (`./start.sh --cli`) |
| `openalgo/` | [marketcalls/openalgo](https://github.com/marketcalls/openalgo) | Broker bridge, web UI, MCP execution |
| `vibetrading/` | [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) | Chat Web UI, plans, OpenAlgo MCP client |

Forks live under [p1927](https://github.com/p1927) (`p1927/TradingAgents`, `p1927/openalgo`, `p1927/Vibe-Trading`) so you can patch and sync upstream like the other submodules.

Trade-level orchestration:

- `start.sh`, `Makefile`, `trade` — run the stack (default: **Vibe Web UI** at http://localhost:5899)
- `docker-compose.stack.yml`, `stack/` — SearXNG + Vibe operator templates
- `integrations/` — OpenAlgo data, news, company research, agent patches
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
├── integrations/      # trade_integrations (OpenAlgo, research hub, agent patches)
├── stack/vibe/        # Vibe agent.json template + trade-stack skill
├── reports/hub/       # shared company research dossiers
├── exposure/          # Cloudflare tunnel + webhook platform URLs
├── scripts/           # sync, setup_vibe.py, ensure_vibe_frontend.sh
├── trade              # unified CLI (stack, tunnel, webhooks, research)
├── start.sh           # default → Vibe Web UI at http://localhost:5899
└── Makefile
```

## Vibe Trading (local fork)

The chat UI runs from the **vibetrading/** submodule, not PyPI:

```bash
git submodule update --init vibetrading
pip install -e vibetrading/          # or ./start.sh (bootstrap does this)
./scripts/ensure_vibe_frontend.sh    # npm install in vibetrading/frontend
./start.sh                           # Vibe Web UI :5899
```

Edit code under `vibetrading/` directly; commit in the fork repo, then bump the submodule pointer in `trade`.
