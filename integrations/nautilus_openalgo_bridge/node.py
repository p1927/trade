"""Bootstrap Nautilus TradingNode for OpenAlgo watch bridge."""

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

    from nautilus_openalgo_bridge.nautilus_config import OpenAlgoDataClientConfig

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


def build_trading_node_config(
    *,
    agent_id: str | None = None,
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

    actor_cfg = {
        "agent_id": agent_id,
        "trigger_vibe": trigger_vibe,
        "alert_cooldown_sec": cfg.alert_cooldown_sec,
    }
    risk_cfg = {
        "agent_id": agent_id,
        "max_daily_loss_inr": 2_000.0,
        "max_open_positions": 3,
        "poll_interval_sec": 60,
    }

    return TradingNodeConfig(
        trader_id=trader_id,
        logging=LoggingConfig(log_level="INFO"),
        cache=cache_config,
        data_clients={
            "OPENALGO": OpenAlgoDataClientConfig(
                poll_interval_ms=cfg.quote_poll_ms,
                watch_symbols=cfg.watch_symbols,
            ),
        },
        actors=[
            ImportableActorConfig(
                actor_path="nautilus_openalgo_bridge.watch_actor:WatchActor",
                config_path="nautilus_openalgo_bridge.watch_actor:WatchActorConfig",
                config=actor_cfg,
            ),
            ImportableActorConfig(
                actor_path="nautilus_openalgo_bridge.bridge_signal_actor:BridgeSignalActor",
                config_path="nautilus_openalgo_bridge.bridge_signal_actor:BridgeSignalActorConfig",
                config={"agent_id": agent_id, "trigger_vibe": trigger_vibe},
            ),
            ImportableActorConfig(
                actor_path="nautilus_openalgo_bridge.risk_actor:RiskActor",
                config_path="nautilus_openalgo_bridge.risk_actor:RiskActorConfig",
                config=risk_cfg,
            ),
        ],
    )


def run_trading_node(
    *,
    agent_id: str | None = None,
    trigger_vibe: bool = True,
    bridge: BridgeConfig | None = None,
) -> int:
    """Build and run the Nautilus watch node (blocks until SIGINT)."""
    if not is_watch_enabled():
        logger.error("NAUTILUS_WATCH_ENABLE=0 — set NAUTILUS_WATCH_ENABLE=true to run watch node")
        return 1

    cfg = bridge or get_bridge_config()
    node_config = build_trading_node_config(
        agent_id=agent_id,
        trigger_vibe=trigger_vibe,
        bridge=cfg,
    )
    from nautilus_openalgo_bridge.factories import OpenAlgoLiveDataClientFactory

    node = TradingNode(config=node_config)
    node.add_data_client_factory("OPENALGO", OpenAlgoLiveDataClientFactory)
    node.build()
    logger.info(
        "Starting Nautilus TradingNode (agent=%s redis=%s)",
        agent_id or "default",
        bool(cfg.redis_url),
    )
    try:
        node.run()
    except KeyboardInterrupt:
        logger.info("Nautilus watch node stopped")
    return 0
