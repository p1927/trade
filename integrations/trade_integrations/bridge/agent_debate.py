"""Run TradingAgents multi-agent debate and persist summary to the shared hub."""

from __future__ import annotations

import copy
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_INDEX_YF = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEI",
    "FINNIFTY": "^NSEI",
    "MIDCPNIFTY": "^NSEI",
    "SENSEX": "^BSESN",
    "^NSEI": "^NSEI",
    "^BSESN": "^BSESN",
}


def _ensure_paths() -> Path:
    """Add integrations + tradingagents to sys.path and register trade hooks."""
    trade_root = Path(__file__).resolve().parents[3]
    integrations = trade_root / "integrations"
    tradingagents = trade_root / "tradingagents"
    for path in (integrations, tradingagents):
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
    import trade_integrations  # noqa: F401

    return trade_root


def to_tradingagents_ticker(ticker: str) -> str:
    raw = ticker.strip().upper()
    if raw in _INDEX_YF:
        return _INDEX_YF[raw]
    if raw.endswith((".NS", ".BO")) or raw.startswith("^"):
        return raw
    if raw in _INDEX_YF.values():
        return raw
    return f"{raw}.NS"


def is_index_ticker(ticker: str) -> bool:
    raw = ticker.strip().upper()
    return raw in _INDEX_YF or raw in _INDEX_YF.values()


def _build_graph_config() -> dict:
    _ensure_paths()
    import tradingagents.default_config as default_config
    from tradingagents.dataflows.config import set_config

    config = copy.deepcopy(default_config.DEFAULT_CONFIG)
    config["data_vendors"]["core_stock_apis"] = os.getenv(
        "TRADINGAGENTS_CORE_STOCK_DATA_VENDOR", "openalgo,yfinance"
    )
    config["data_vendors"]["technical_indicators"] = os.getenv(
        "TRADINGAGENTS_TECHNICAL_INDICATORS_DATA_VENDOR", "openalgo,yfinance"
    )
    config["data_vendors"]["news_data"] = os.getenv(
        "TRADINGAGENTS_NEWS_DATA_VENDOR", "aggregated"
    )
    config["max_debate_rounds"] = int(os.getenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "1"))
    config["max_risk_discuss_rounds"] = int(os.getenv("TRADINGAGENTS_MAX_RISK_ROUNDS", "1"))
    set_config(config)
    return config


def run_agent_debate(
    ticker: str,
    *,
    trade_date: str | None = None,
    asset_type: str = "stock",
) -> dict:
    """Execute TradingAgents graph and save debate summary under reports/hub."""
    _ensure_paths()
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    from trade_integrations.context.hub import save_agent_debate

    display_ticker = ticker.strip().upper()
    graph_ticker = to_tradingagents_ticker(display_ticker)
    analysis_date = trade_date or datetime.now().strftime("%Y-%m-%d")

    config = _build_graph_config()
    if is_index_ticker(display_ticker):
        selected_analysts = ("market", "social", "news")
    else:
        selected_analysts = ("market", "social", "news", "fundamentals")

    graph = TradingAgentsGraph(
        selected_analysts=selected_analysts,
        debug=False,
        config=config,
    )
    final_state, rating = graph.propagate(graph_ticker, analysis_date, asset_type=asset_type)

    debate = final_state.get("investment_debate_state") or {}
    risk = final_state.get("risk_debate_state") or {}
    payload = {
        "ticker": display_ticker,
        "graph_ticker": graph_ticker,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "trade_date": final_state.get("trade_date") or analysis_date,
        "rating": rating,
        "asset_type": asset_type,
        "investment_debate": {
            "bull_summary": debate.get("bull_history"),
            "bear_summary": debate.get("bear_history"),
            "judge_decision": debate.get("judge_decision"),
        },
        "risk_debate": {
            "aggressive_summary": risk.get("aggressive_history"),
            "conservative_summary": risk.get("conservative_history"),
            "neutral_summary": risk.get("neutral_history"),
            "judge_decision": risk.get("judge_decision"),
        },
        "final_trade_decision": final_state.get("final_trade_decision"),
        "analyst_reports": {
            "market": final_state.get("market_report"),
            "sentiment": final_state.get("sentiment_report"),
            "news": final_state.get("news_report"),
            "fundamentals": final_state.get("fundamentals_report"),
        },
    }
    save_agent_debate(display_ticker, payload)
    logger.info("Saved agent debate for %s (rating=%s)", display_ticker, rating)
    return payload
