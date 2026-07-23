"""Environment configuration for autonomous agent trading sessions."""

from __future__ import annotations

import os
from dataclasses import dataclass


_TRUE = {"1", "true", "yes", "on"}


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in _TRUE


@dataclass(frozen=True)
class AgentTradingConfig:
    enabled: bool = False
    budget_inr: float = 20_000.0
    max_daily_loss_inr: float = 2_000.0
    min_strategy_score: float = 0.45
    watchlist: tuple[str, ...] = ()
    poll_cron: str = "*/5 * * * *"
    product: str = "NRML"
    market_open: str = "09:20"
    market_close: str = "15:10"
    max_open_positions: int = 1
    enable_scheduler: bool = False
    min_hold_minutes: int = 10
    poll_interval_ms: int = 300_000


def get_agent_trading_config() -> AgentTradingConfig:
    """Load autonomous agent trading defaults from environment."""
    watchlist_raw = os.getenv("AUTONOMOUS_AGENT_TRADING_WATCHLIST", "").strip()
    watchlist = tuple(
        item.strip().upper()
        for item in watchlist_raw.split(",")
        if item.strip()
    )
    return AgentTradingConfig(
        enabled=_env_bool("AUTONOMOUS_AGENT_TRADING_ENABLED", "false"),
        budget_inr=float(os.getenv("AUTONOMOUS_AGENT_TRADING_BUDGET_INR", "20000")),
        max_daily_loss_inr=float(os.getenv("AUTONOMOUS_AGENT_TRADING_MAX_DAILY_LOSS_INR", "2000")),
        min_strategy_score=float(os.getenv("AUTONOMOUS_AGENT_TRADING_MIN_STRATEGY_SCORE", "0.45")),
        watchlist=watchlist,
        poll_cron=os.getenv("AUTONOMOUS_AGENT_TRADING_POLL_CRON", "*/5 * * * *"),
        product=os.getenv("AUTONOMOUS_AGENT_TRADING_PRODUCT", "NRML").strip().upper() or "NRML",
        market_open=os.getenv("AUTONOMOUS_AGENT_TRADING_MARKET_OPEN", "09:20"),
        market_close=os.getenv("AUTONOMOUS_AGENT_TRADING_MARKET_CLOSE", "15:10"),
        max_open_positions=int(os.getenv("AUTONOMOUS_AGENT_TRADING_MAX_OPEN_POSITIONS", "1")),
        enable_scheduler=_env_bool("AUTONOMOUS_AGENT_TRADING_ENABLE_SCHEDULER", "false"),
        min_hold_minutes=int(os.getenv("AUTONOMOUS_AGENT_TRADING_MIN_HOLD_MINUTES", "10")),
        poll_interval_ms=int(os.getenv("AUTONOMOUS_AGENT_TRADING_POLL_INTERVAL_MS", "300000")),
    )


def is_agent_trading_enabled() -> bool:
    """Return True when env master switch or any autonomous agent is running."""
    if get_agent_trading_config().enabled:
        return True
    try:
        from trade_integrations.autonomous_agents.store import list_agents

        return any(str(a.get("status") or "") == "running" for a in list_agents())
    except Exception:
        return False
