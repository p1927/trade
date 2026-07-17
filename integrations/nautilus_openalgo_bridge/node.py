"""Bootstrap Nautilus TradingNode for OpenAlgo + Alpaca watch bridge."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from nautilus_openalgo_bridge.config import BridgeConfig, get_bridge_config, is_watch_enabled

logger = logging.getLogger(__name__)

NAUTILUS_AVAILABLE = False
_NAUTILUS_IMPORT_ERROR: str | None = None

try:
    import nautilus_trader  # noqa: F401
    from nautilus_trader.cache.config import CacheConfig
    from nautilus_trader.common.config import DatabaseConfig, ImportableActorConfig, LoggingConfig
    from nautilus_trader.config import TradingNodeConfig
    from nautilus_trader.live.node import TradingNode
    from nautilus_trader.model.identifiers import TraderId

    from nautilus_openalgo_bridge.nautilus_config import AlpacaDataClientConfig, OpenAlgoDataClientConfig

    NAUTILUS_AVAILABLE = True
except ImportError as exc:
    _NAUTILUS_IMPORT_ERROR = str(exc)


def nautilus_import_error() -> str | None:
    return _NAUTILUS_IMPORT_ERROR


def _database_config(bridge: BridgeConfig) -> DatabaseConfig | None:
    url = (bridge.redis_url or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    return DatabaseConfig(
        type="redis",
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 6379,
        password=parsed.password,
    )


def _resolve_agent_ids(*, agent_ids: list[str] | None, agent_id: str | None) -> list[str]:
    ids = [str(a).strip() for a in (agent_ids or []) if str(a).strip()]
    if agent_id and agent_id.strip() and agent_id.strip() not in ids:
        ids.append(agent_id.strip())
    if ids:
        return ids
    try:
        from nautilus_openalgo_bridge.registry_paths import read_registry_agent_ids

        reg = read_registry_agent_ids()
        if reg:
            return reg
    except Exception:
        pass
    try:
        from trade_integrations.autonomous_agents.nautilus_watch import get_registry_agent_ids

        reg = get_registry_agent_ids()
        if reg:
            return reg
    except Exception:
        pass
    return []


def _ensure_handoffs_for_agents(agent_ids: list[str]) -> None:
    """Create handoff shells with watch_spec so WatchActor loads rules on start."""
    from nautilus_openalgo_bridge.handoff import ensure_handoff_for_agent

    for aid in agent_ids:
        try:
            ensure_handoff_for_agent(aid)
        except Exception as exc:
            logger.debug("handoff ensure skipped for %s: %s", aid, exc)


def _agent_market(agent_id: str) -> str:
    try:
        from nautilus_openalgo_bridge.registry_paths import registry_agent_market

        market = registry_agent_market(agent_id)
        if market:
            return market
    except Exception:
        pass
    try:
        from nautilus_openalgo_bridge.agent_limits import agent_market_code

        return agent_market_code(agent_id)
    except Exception:
        return "IN"


def _collect_watch_symbols(agent_ids: list[str], bridge: BridgeConfig) -> tuple[tuple[str, ...], tuple[str, ...]]:
    in_symbols: set[str] = set(bridge.watch_symbols)
    us_symbols: set[str] = set()
    for aid in agent_ids:
        market = _agent_market(aid)
        try:
            from trade_integrations.autonomous_agents.store import get_agent

            agent = get_agent(aid) or {}
            syms = [str(s).upper() for s in (agent.get("symbols") or [])]
        except Exception:
            syms = []
        if market == "US":
            us_symbols.update(syms or ["SPY"])
        else:
            in_symbols.update(syms or ["NIFTY"])
    if not in_symbols:
        in_symbols = set(bridge.watch_symbols)
    if not us_symbols and any(_agent_market(a) == "US" for a in agent_ids):
        us_symbols.add("SPY")
    return tuple(sorted(in_symbols)), tuple(sorted(us_symbols))


def build_trading_node_config(
    *,
    agent_id: str | None = None,
    agent_ids: list[str] | None = None,
    trigger_vibe: bool = True,
    bridge: BridgeConfig | None = None,
) -> Any:
    if not NAUTILUS_AVAILABLE:
        raise RuntimeError(
            f"nautilus_trader not available ({_NAUTILUS_IMPORT_ERROR}). "
            "Run ./scripts/setup_nautilus.sh and use .venv-nautilus."
        )
    cfg = bridge or get_bridge_config()
    trader_id = TraderId(f"TRADE-WATCH-{cfg.instance_id.replace('_', '-')[:20]}")

    cache_config = CacheConfig()
    db = _database_config(cfg)
    if db is not None:
        cache_config = CacheConfig(database=db, flush_on_start=False)

    ids = _resolve_agent_ids(agent_ids=agent_ids, agent_id=agent_id)
    if not ids and agent_id:
        ids = [agent_id]
    if ids:
        _ensure_handoffs_for_agents(ids)

    in_symbols, us_symbols = _collect_watch_symbols(ids, cfg)

    actors: list[ImportableActorConfig] = []
    for aid in ids or [None]:
        actor_suffix = (aid or "default").replace("_", "-")
        actor_cfg = {
            "component_id": f"WatchActor-{actor_suffix}",
            "agent_id": aid,
            "trigger_vibe": trigger_vibe,
            "alert_cooldown_sec": cfg.alert_cooldown_sec,
            "market": _agent_market(aid) if aid else "IN",
            "watch_symbols": list(us_symbols if aid and _agent_market(aid) == "US" else in_symbols),
        }
        actors.append(
            ImportableActorConfig(
                actor_path="nautilus_openalgo_bridge.watch_actor:WatchActor",
                config_path="nautilus_openalgo_bridge.watch_actor:WatchActorConfig",
                config=actor_cfg,
            )
        )
        actors.append(
            ImportableActorConfig(
                actor_path="nautilus_openalgo_bridge.bridge_signal_actor:BridgeSignalActor",
                config_path="nautilus_openalgo_bridge.bridge_signal_actor:BridgeSignalActorConfig",
                config={
                    "component_id": f"BridgeSignalActor-{actor_suffix}",
                    "agent_id": aid,
                    "trigger_vibe": trigger_vibe,
                },
            )
        )

    if not actors:
        raise RuntimeError(
            "No watch agents configured — pass --agent-id, --registry with agents in "
            "log/nautilus-watch.agents.json, or set NAUTILUS_AGENT_ID"
        )

    from nautilus_openalgo_bridge.agent_limits import (
        max_daily_loss_for_agent,
        max_open_positions_for_agent,
    )

    seen_risk: set[str] = set()
    for aid in ids:
        if not aid or aid in seen_risk:
            continue
        seen_risk.add(aid)
        market = _agent_market(aid) if aid else "IN"
        risk_suffix = aid.replace("_", "-")
        risk_cfg = {
            "component_id": f"RiskActor-{risk_suffix}",
            "agent_id": aid,
            "market": market,
            "max_daily_loss_inr": max_daily_loss_for_agent(aid),
            "max_open_positions": max_open_positions_for_agent(aid),
            "poll_interval_sec": 60,
        }
        actors.append(
            ImportableActorConfig(
                actor_path="nautilus_openalgo_bridge.risk_actor:RiskActor",
                config_path="nautilus_openalgo_bridge.risk_actor:RiskActorConfig",
                config=risk_cfg,
            )
        )

    if not seen_risk:
        actors.append(
            ImportableActorConfig(
                actor_path="nautilus_openalgo_bridge.risk_actor:RiskActor",
                config_path="nautilus_openalgo_bridge.risk_actor:RiskActorConfig",
                config={
                    "agent_id": None,
                    "market": "IN",
                    "max_daily_loss_inr": 2_000.0,
                    "max_open_positions": 3,
                    "poll_interval_sec": 60,
                },
            )
        )

    data_clients: dict[str, Any] = {}
    if in_symbols:
        data_clients["OPENALGO"] = OpenAlgoDataClientConfig(
            poll_interval_ms=cfg.quote_poll_ms,
            watch_symbols=in_symbols,
        )
    if us_symbols:
        data_clients["ALPACA"] = AlpacaDataClientConfig(
            poll_interval_ms=cfg.quote_poll_ms,
            watch_symbols=us_symbols,
        )

    return TradingNodeConfig(
        trader_id=trader_id,
        logging=LoggingConfig(log_level="INFO"),
        cache=cache_config,
        data_clients=data_clients,
        actors=actors,
    )


def run_trading_node(
    *,
    agent_id: str | None = None,
    agent_ids: list[str] | None = None,
    trigger_vibe: bool = True,
    bridge: BridgeConfig | None = None,
    use_registry: bool = False,
) -> int:
    """Build and run the Nautilus watch node (blocks until SIGINT)."""
    if not is_watch_enabled():
        logger.error("NAUTILUS_WATCH_ENABLE=0 — set NAUTILUS_WATCH_ENABLE=true to run watch node")
        return 1

    cfg = bridge or get_bridge_config()
    ids = list(agent_ids or [])
    if use_registry:
        from nautilus_openalgo_bridge.registry_paths import read_registry_agent_ids

        ids = read_registry_agent_ids()
        if not ids:
            try:
                from trade_integrations.autonomous_agents.nautilus_watch import get_registry_agent_ids

                ids = get_registry_agent_ids()
            except Exception:
                ids = []
        if not ids:
            logger.error(
                "Registry mode but no agents in log/nautilus-watch.agents.json — "
                "commit an agent or pass --agent-id aa_*"
            )
            return 1

    if not ids and not agent_id:
        logger.error("No agent id — use --registry or --agent-id aa_*")
        return 1

    node_config = build_trading_node_config(
        agent_id=agent_id,
        agent_ids=ids or None,
        trigger_vibe=trigger_vibe,
        bridge=cfg,
    )
    from nautilus_openalgo_bridge.factories import AlpacaLiveDataClientFactory, OpenAlgoLiveDataClientFactory

    node = TradingNode(config=node_config)
    if "OPENALGO" in node_config.data_clients:
        node.add_data_client_factory("OPENALGO", OpenAlgoLiveDataClientFactory)
    if "ALPACA" in node_config.data_clients:
        node.add_data_client_factory("ALPACA", AlpacaLiveDataClientFactory)
    node.build()
    logger.info(
        "Starting Nautilus TradingNode (agents=%s redis=%s)",
        ids or agent_id or "default",
        bool(cfg.redis_url),
    )
    try:
        node.run()
    except KeyboardInterrupt:
        logger.info("Nautilus watch node stopped")
    return 0
