"""Market session helpers for autonomous agents (OpenAlgo authority path)."""

from __future__ import annotations


def is_trading_session_open(*, market: str = "IN") -> bool:
    """Return True when the agent's market session is open (simulator, Nautilus, or legacy cfg)."""
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
    try:
        from trade_integrations.auto_paper.config import get_auto_paper_config
        from trade_integrations.auto_paper.engine import is_market_session_open

        return is_market_session_open(get_auto_paper_config())
    except Exception:
        return True
