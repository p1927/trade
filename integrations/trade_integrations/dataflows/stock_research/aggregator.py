"""Pipeline orchestrator for stock trade plans."""

from __future__ import annotations

import os

from datetime import datetime, timezone

from trade_integrations.context.hub import load_company_research_json, save_company_research
from trade_integrations.dataflows.company_research.models import StageResult
from trade_integrations.dataflows.openalgo import fetch_openalgo_quote

from .browse_summary import build_stock_browse_summary
from .format import format_stock_report
from .models import StockResearchDoc
from .payoff_charges import build_stock_payoff, calculate_equity_charges
from .strategy_ranker import build_stock_scenarios, rank_stock_strategies


def _strategy_builder_base() -> str:
    host = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5001").rstrip("/")
    return f"{host}/strategybuilder"


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _company_payload(doc) -> dict:
    if doc is None:
        return {}
    from dataclasses import asdict

    return asdict(doc)


def run_stock_research(ticker: str, *, lookahead_days: int = 14) -> StockResearchDoc:
    """Build a stock trade plan from company research + live quote."""
    now = _stage_now()
    sym = ticker.strip().upper().replace(".NS", "").replace(".BO", "")

    company = load_company_research_json(sym)
    if company is None:
        from trade_integrations.dataflows.company_research.aggregator import run_company_research

        company = run_company_research(sym, lookahead_days=lookahead_days)
        save_company_research(company)

    payload = _company_payload(company)
    identity = payload.get("identity") or {}
    quote = None
    try:
        q = fetch_openalgo_quote(sym)
        if q:
            quote = {"ltp": q.get("ltp"), "volume": q.get("volume"), "source": "openalgo"}
    except Exception:
        quote = None

    spot = float((quote or {}).get("ltp") or identity.get("last_price") or 0)
    browse = build_stock_browse_summary(
        ticker=sym,
        identity=identity,
        quote=quote,
        peers=payload.get("peers"),
    )

    ranked = rank_stock_strategies(payload, spot=spot) if spot > 0 else []
    scenarios = build_stock_scenarios(payload.get("calendar_events") or [], ranked)
    sentiment = payload.get("sentiment") or {}
    view = "bullish" if (sentiment.get("score") or 0) > 0.15 else "bearish" if (sentiment.get("score") or 0) < -0.15 else "neutral"

    doc = StockResearchDoc(
        ticker=sym,
        as_of=now,
        lookahead_days=lookahead_days,
        market=payload.get("market") or "IN",
        spot=spot or None,
        browse_summary=browse,
        events=list(payload.get("calendar_events") or []),
        scenarios=scenarios,
        ranked_strategies=ranked,
        prediction={
            "view": view,
            "horizon_days": lookahead_days,
            "confidence": ranked[0]["score"] if ranked else 0,
            "sentiment": sentiment.get("score"),
        },
        stages=[
            StageResult(
                stage="company_research",
                status="ok",
                vendor="hub",
                fetched_at=now,
                data={"ticker": sym},
            )
        ],
    )

    if ranked:
        top = ranked[0]
        legs = [
            {
                "symbol": sym,
                "side": top.get("action", "BUY"),
                "price": spot,
                "quantity": top.get("quantity", 1),
                "product": top.get("product", "CNC"),
            }
        ]
        doc.recommended = dict(top)
        doc.charges = calculate_equity_charges(legs, product=top.get("product", "CNC"))
        doc.payoff = build_stock_payoff(
            spot,
            int(top.get("quantity", 1)),
            target=top.get("target"),
            stop=top.get("stop"),
        )
        doc.implementation_steps = [
            {"step": 1, "action": "preview", "description": "Review entry, target, stop"},
            {
                "step": 2,
                "action": "funds",
                "description": "Check available cash for CNC buy",
                "mcp_tool": "get_funds",
            },
            {
                "step": 3,
                "action": "confirm",
                "description": "User confirms stock order",
            },
            {
                "step": 4,
                "action": "execute",
                "description": "Place CNC order",
                "mcp_tool": "place_order",
                "payload": {
                    "symbol": sym,
                    "exchange": "NSE",
                    "action": top.get("action", "BUY"),
                    "quantity": top.get("quantity", 1),
                    "product": "CNC",
                    "pricetype": "MARKET",
                },
            },
        ]
        doc.meta["strategy_builder_url"] = f"{_strategy_builder_base()}?plan={sym}&asset=stock"

    return doc
