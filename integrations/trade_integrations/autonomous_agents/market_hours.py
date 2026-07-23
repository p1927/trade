"""Market session helpers for autonomous agents (OpenAlgo authority path)."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def is_trading_session_open(*, market: str = "IN") -> bool:
    """Return True when the agent's market session is open (simulator or Nautilus)."""
    region = str(market or "IN").upper()
    if region == "IN":
        try:
            from trade_integrations.stock_simulator.integration import sim_market_session_open

            if sim_market_session_open(market="IN"):
                return True
        except Exception:
            pass
    try:
        from nautilus_openalgo_bridge.market_hours import is_market_open_for_market

        return is_market_open_for_market(region)
    except Exception:
        pass
    from trade_integrations.autonomous_agents.trading_config import get_agent_trading_config

    cfg = get_agent_trading_config()
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    start = _parse_hhmm(cfg.market_open)
    end = _parse_hhmm(cfg.market_close)
    return start <= now.time() <= end


def minutes_to_session_close(cfg) -> int | None:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return None
    end = _parse_hhmm(cfg.market_close)
    close_dt = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if now.time() > end:
        return 0
    return max(0, int((close_dt - now).total_seconds() / 60))
