"""Trading-session gates for Nautilus watch (IN NSE + US equity RTH)."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from nautilus_openalgo_bridge.config import get_bridge_config, is_bridge_market_open

IST = ZoneInfo("Asia/Kolkata")
US_EAST = ZoneInfo("America/New_York")

_US_RTH_OPEN = time(9, 30)
_US_RTH_CLOSE = time(16, 0)

# When session is closed, poll/dispatch loops sleep longer (default 5 min).
_CLOSED_POLL_SEC = max(60, int(__import__("os").getenv("NAUTILUS_CLOSED_MARKET_POLL_SEC", "300")))


def closed_market_poll_interval_sec() -> float:
    return float(_CLOSED_POLL_SEC)


def is_us_market_session_open(*, now: datetime | None = None) -> bool:
    """US equity regular hours (Mon–Fri 09:30–16:00 ET)."""
    now = (now or datetime.now(US_EAST)).astimezone(US_EAST)
    if now.weekday() >= 5:
        return False
    current = now.time()
    return _US_RTH_OPEN <= current <= _US_RTH_CLOSE


def is_us_exit_window_open(*, now: datetime | None = None) -> bool:
    """US RTH plus 20-minute grace after close (mirrors India exit window)."""
    from datetime import timedelta

    now = (now or datetime.now(US_EAST)).astimezone(US_EAST)
    if now.weekday() >= 5:
        return False
    if is_us_market_session_open(now=now):
        return True
    close_dt = datetime.combine(now.date(), _US_RTH_CLOSE, tzinfo=US_EAST)
    grace_end = close_dt + timedelta(minutes=20)
    return close_dt.time() < now.time() <= grace_end.time()


def is_exit_window_open_for_agent(agent_id: str | None, *, now: datetime | None = None) -> bool:
    """Market-aware EXIT window for autonomous agents (IN IST + grace, US ET + grace)."""
    market = agent_market(agent_id)
    if market == "US":
        return is_us_exit_window_open(now=now)
    from nautilus_openalgo_bridge.config import get_bridge_config, is_bridge_exit_window_open

    return is_bridge_exit_window_open(get_bridge_config(), now=now)


def is_in_market_session_open(*, now: datetime | None = None) -> bool:
    """India NSE intraday window from bridge config."""
    try:
        from trade_integrations.stock_simulator.integration import sim_market_session_open

        if sim_market_session_open(market="IN"):
            return True
    except Exception:
        pass
    now_ist = (now or datetime.now(IST)).astimezone(IST)
    return is_bridge_market_open(get_bridge_config(), now=now_ist)


def is_market_open_for_market(market: str, *, now: datetime | None = None) -> bool:
    m = str(market or "IN").strip().upper()
    if m == "US":
        return is_us_market_session_open(now=now)
    return is_in_market_session_open(now=now)


def agent_market(agent_id: str | None) -> str:
    if not agent_id:
        return "IN"
    if str(agent_id).startswith("ws_"):
        try:
            from trade_integrations.autonomous_agents.nautilus_watch import list_registry_agents

            for row in list_registry_agents():
                if str(row.get("agent_id") or "") == agent_id:
                    return str(row.get("market") or "IN").upper()
        except Exception:
            pass
        return "IN"
    try:
        from trade_integrations.autonomous_agents.store import get_agent
        from trade_integrations.execution.profile import resolve_profile

        agent = get_agent(agent_id) or {}
        profile = resolve_profile(agent=agent)
        return "US" if profile.is_us else "IN"
    except Exception:
        return "IN"


def agent_market_hours_only(agent_id: str | None) -> bool:
    if not agent_id:
        return True
    try:
        from trade_integrations.autonomous_agents.mandate_config import mandate_config_from_agent
        from trade_integrations.autonomous_agents.store import get_agent

        agent = get_agent(agent_id) or {}
        return bool(mandate_config_from_agent(agent).market_hours_only)
    except Exception:
        return True


def is_agent_watch_session_open(agent_id: str | None, *, now: datetime | None = None) -> bool:
    """True when this agent's market session is open (respects market_hours_only)."""
    if not agent_market_hours_only(agent_id):
        return True
    return is_market_open_for_market(agent_market(agent_id), now=now)


def any_trading_market_open(*, now: datetime | None = None) -> bool:
    return is_in_market_session_open(now=now) or is_us_market_session_open(now=now)


def market_closed_skip_reason(agent_id: str | None = None) -> dict[str, Any]:
    market = agent_market(agent_id)
    return {
        "status": "skipped",
        "reason": "outside_market_hours",
        "market": market,
        "session_open": is_market_open_for_market(market),
    }
