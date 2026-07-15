# Trade Stack

Unified wrapper around two submodules:

| Path | Submodule | Upstream |
|------|-----------|----------|
| `tradingagents/` | [p1927/TradingAgents](https://github.com/p1927/TradingAgents) | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) |
| `openalgo/` | [p1927/openalgo](https://github.com/p1927/openalgo) | [marketcalls/openalgo](https://github.com/marketcalls/openalgo) |

Trade-level files (this repo) own stack orchestration and integrations:

- `start.sh`, `Makefile` — run the full stack
- `docker-compose.stack.yml`, `stack/` — SearXNG
- `integrations/` — TradingAgents extensions (OpenAlgo bridge, SearXNG news, RSS feeds)
- `scripts/sync.sh` — pull upstream changes into both submodules

## Clone

```bash
git clone --recurse-submodules https://github.com/p1927/trade.git
cd trade
cp .env.example .env   # configure keys
./start.sh
```

## Sync upstream

```bash
make sync-status   # see what's new upstream
make sync          # merge upstream into both submodules
```

## Layout

```
trade/
├── tradingagents/     # submodule — AI trading engine
├── openalgo/          # submodule — broker bridge + UI
├── integrations/      # trade_integrations package (OpenAlgo, SearXNG, RSS overlays)
├── scripts/           # sync and utilities
├── stack/             # SearXNG config
├── start.sh           # bootstrap + launch
└── Makefile
```
