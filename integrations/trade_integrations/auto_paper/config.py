"""Environment configuration for automated paper trading."""

from __future__ import annotations

import os
from dataclasses import dataclass


_TRUE = {"1", "true", "yes", "on"}


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in _TRUE


@dataclass(frozen=True)
class AutoPaperConfig:
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


def get_auto_paper_config() -> AutoPaperConfig:
    """Load auto paper trading settings from environment."""
    watchlist_raw = os.getenv("AUTO_PAPER_WATCHLIST", "").strip()
    watchlist = tuple(
        item.strip().upper()
        for item in watchlist_raw.split(",")
        if item.strip()
    )
    return AutoPaperConfig(
        enabled=_env_bool("AUTO_PAPER_TRADING_ENABLED", "false"),
        budget_inr=float(os.getenv("AUTO_PAPER_BUDGET_INR", "20000")),
        max_daily_loss_inr=float(os.getenv("AUTO_PAPER_MAX_DAILY_LOSS_INR", "2000")),
        min_strategy_score=float(os.getenv("AUTO_PAPER_MIN_STRATEGY_SCORE", "0.45")),
        watchlist=watchlist,
        poll_cron=os.getenv("AUTO_PAPER_POLL_CRON", "*/5 * * * *"),
        product=os.getenv("AUTO_PAPER_PRODUCT", "NRML").strip().upper() or "NRML",
        market_open=os.getenv("AUTO_PAPER_MARKET_OPEN", "09:20"),
        market_close=os.getenv("AUTO_PAPER_MARKET_CLOSE", "15:10"),
        max_open_positions=int(os.getenv("AUTO_PAPER_MAX_OPEN_POSITIONS", "1")),
        enable_scheduler=_env_bool("AUTO_PAPER_ENABLE_SCHEDULER", "false"),
        min_hold_minutes=int(os.getenv("AUTO_PAPER_MIN_HOLD_MINUTES", "10")),
        poll_interval_ms=int(os.getenv("AUTO_PAPER_POLL_INTERVAL_MS", "300000")),
    )


def is_auto_paper_active() -> bool:
    """Return True when env master switch or runtime session is enabled."""
    if get_auto_paper_config().enabled:
        return True
    from trade_integrations.auto_paper.session_store import load_session

    session = load_session()
    return bool(session.get("enabled"))
