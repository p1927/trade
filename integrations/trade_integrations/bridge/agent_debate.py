"""Run TradingAgents multi-agent debate and persist summary to the shared hub."""

from __future__ import annotations

import copy
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_debate_running: set[str] = set()
_debate_lock = threading.Lock()

_INDEX_YF = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "^CNXFIN",
    "MIDCPNIFTY": "^NSEMDCP50",
    "NIFTYMIDSELECT": "^NSEMDCP50",
    "NIFTYIT": "^CNXIT",
    "SENSEX": "^BSESN",
    "^NSEI": "^NSEI",
    "^BSESN": "^BSESN",
    "^CNXFIN": "^CNXFIN",
    "^NSEMDCP50": "^NSEMDCP50",
    "^CNXIT": "^CNXIT",
}

# Indices without confirmed yfinance symbols — block debate until mapped.
_DEBATE_BLOCKED_INDICES: frozenset[str] = frozenset()


def debate_eligible_for_ticker(ticker: str) -> tuple[bool, str | None]:
    """Return (eligible, block_reason) for TradingAgents debate."""
    raw = ticker.strip().upper()
    if raw in _DEBATE_BLOCKED_INDICES:
        return False, f"debate unavailable for {raw} until yfinance symbol is confirmed"
    if raw in _INDEX_YF or raw in _INDEX_YF.values():
        return True, None
    return True, None


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

    from trade_integrations.bridge.hub_context import (
        build_tradingagents_index_context,
        build_tradingagents_options_context,
        infer_debate_asset_type,
    )
    from trade_integrations.context.hub import save_agent_debate
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso

    display_ticker = ticker.strip().upper()
    resolved_asset = infer_debate_asset_type(display_ticker, asset_type)
    graph_ticker = to_tradingagents_ticker(display_ticker)
    analysis_date = trade_date or india_trading_date_iso()

    if not is_index_ticker(display_ticker):
        try:
            from tradingagents.dataflows.errors import NoMarketDataError
            from tradingagents.dataflows.stockstats_utils import load_ohlcv

            load_ohlcv(graph_ticker, analysis_date)
        except NoMarketDataError as exc:
            raise ValueError(
                f"Agent debate unavailable for {display_ticker}: no OHLCV history"
            ) from exc

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

    hub_options_context = build_tradingagents_options_context(display_ticker, asset_type=resolved_asset)
    hub_index_context = build_tradingagents_index_context(display_ticker)
    hub_past_context = f"{hub_index_context}{hub_options_context}".strip()
    if hub_past_context:
        original_run_graph = graph._run_graph

        def _run_graph_with_hub(company_name, trade_date_arg, asset_type=resolved_asset):
            original_create = graph.propagator.create_initial_state

            def _create_with_hub(*args, **kwargs):
                state = original_create(*args, **kwargs)
                prev = state.get("past_context") or ""
                state["past_context"] = f"{prev}{hub_past_context}".strip()
                return state

            graph.propagator.create_initial_state = _create_with_hub
            try:
                return original_run_graph(company_name, trade_date_arg, asset_type=asset_type)
            finally:
                graph.propagator.create_initial_state = original_create

        graph._run_graph = _run_graph_with_hub

    final_state, rating = graph.propagate(graph_ticker, analysis_date, asset_type=resolved_asset)

    debate = final_state.get("investment_debate_state") or {}
    risk = final_state.get("risk_debate_state") or {}
    payload = {
        "ticker": display_ticker,
        "graph_ticker": graph_ticker,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "trade_date": final_state.get("trade_date") or analysis_date,
        "rating": rating,
        "asset_type": resolved_asset,
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
    refresh_hub_research_after_debate(display_ticker, asset_type=resolved_asset)
    return payload


def is_debate_running(ticker: str) -> bool:
    return ticker.strip().upper() in _debate_running


def run_agent_debate_locked(
    ticker: str,
    *,
    trade_date: str | None = None,
    asset_type: str = "stock",
    allow_stale_cache: bool = True,
) -> dict:
    """Run debate with a process-wide lock; return fresh cache if already running."""
    key = ticker.strip().upper()
    with _debate_lock:
        if key in _debate_running:
            from trade_integrations.context.hub import is_agent_debate_cache_fresh, load_agent_debate_json

            cached = load_agent_debate_json(key)
            if cached:
                if is_agent_debate_cache_fresh(key) or allow_stale_cache:
                    return cached
            raise RuntimeError(f"Agent debate already running for {key}")
        _debate_running.add(key)
    try:
        return run_agent_debate(ticker, trade_date=trade_date, asset_type=asset_type)
    finally:
        with _debate_lock:
            _debate_running.discard(key)


def refresh_hub_research_after_debate(ticker: str, *, asset_type: str | None = None) -> None:
    """Re-run hub aggregators so debate_synthesis re-ranks strategies after debate."""
    from trade_integrations.bridge.hub_context import infer_debate_asset_type
    from trade_integrations.research.orchestrator import ensure_research_complete
    from trade_integrations.research.registry import ResearchKind, eligible_kinds_for_ticker, resolve_kind_for_ticker

    sym = ticker.strip().upper()
    resolved = infer_debate_asset_type(sym, asset_type)
    prefer = ResearchKind.OPTIONS if resolved == "options" else ResearchKind.STOCK
    primary = resolve_kind_for_ticker(sym, prefer=prefer)
    if primary is None:
        return
    kinds: list[ResearchKind] = [primary]
    eligible = set(eligible_kinds_for_ticker(sym))
    if ResearchKind.INDEX in eligible and primary != ResearchKind.INDEX:
        kinds.append(ResearchKind.INDEX)
    if (
        ResearchKind.OPTIONS in eligible
        and primary != ResearchKind.OPTIONS
        and resolved != "stock"
    ):
        kinds.append(ResearchKind.OPTIONS)
    for kind in dict.fromkeys(kinds):
        try:
            ensure_research_complete(sym, kind=kind, refresh=True)
            logger.info("Refreshed hub %s research after debate for %s", kind.value, sym)
        except Exception:
            logger.exception("Hub refresh after debate failed for %s (%s)", sym, kind.value)
