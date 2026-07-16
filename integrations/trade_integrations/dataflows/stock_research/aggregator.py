"""Pipeline orchestrator for stock trade plans."""

from __future__ import annotations

import os

from datetime import datetime, timezone

from trade_integrations.context.hub import load_company_research_json, save_company_research
from trade_integrations.dataflows.company_research.models import StageResult
from trade_integrations.dataflows.market_quotes import fetch_live_quote

from .browse_summary import build_stock_browse_summary
from .format import format_stock_report
from .models import StockResearchDoc
from .payoff_charges import build_stock_payoff, calculate_equity_charges
from .predictor import predict_stock
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
        q = fetch_live_quote(sym)
        if q:
            quote = {"ltp": q.get("ltp"), "volume": q.get("volume"), "source": q.get("source") or "live"}
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

    from trade_integrations.context.hub import load_agent_debate_json
    from trade_integrations.research.debate_synthesis import (
        apply_debate_bias_to_stock_ranked,
        extract_structured_debate,
        merge_stock_prediction,
    )

    events = payload.get("calendar_events") or []
    earnings_near = any("earn" in str(e.get("type", "")).lower() for e in events[:3])
    quant = predict_stock(sym, spot, horizon_days=lookahead_days, earnings_widen=earnings_near)
    debate_raw = load_agent_debate_json(sym)
    debate_struct = extract_structured_debate(debate_raw)
    merged_prediction = merge_stock_prediction(
        debate_struct,
        quant,
        spot=spot,
        horizon_days=lookahead_days,
    )

    if ranked and debate_struct.get("view"):
        ranked = apply_debate_bias_to_stock_ranked(
            ranked,
            debate_view=debate_struct.get("view"),
            debate_confidence=float(debate_struct.get("direction_confidence") or 0.0),
        )

    if ranked and merged_prediction.get("target"):
        ranked[0]["target"] = merged_prediction["target"]
    if ranked and merged_prediction.get("stop"):
        ranked[0]["stop"] = merged_prediction["stop"]

    doc = StockResearchDoc(
        ticker=sym,
        as_of=now,
        lookahead_days=lookahead_days,
        market=payload.get("market") or "IN",
        spot=spot or None,
        browse_summary=browse,
        events=list(events),
        scenarios=scenarios,
        ranked_strategies=ranked,
        prediction=merged_prediction,
        stages=[
            StageResult(
                stage="company_research",
                status="ok",
                vendor="hub",
                fetched_at=now,
                data={"ticker": sym},
            ),
            StageResult(
                stage="stock_quant_predict",
                status="ok",
                vendor=quant.get("source") or "quant",
                fetched_at=now,
                data={"horizon_days": lookahead_days},
            ),
        ],
    )
    if debate_raw:
        doc.stages.append(
            StageResult(
                stage="debate_synthesis",
                status="ok",
                vendor="agent_debate",
                fetched_at=now,
                data={"debate_as_of": debate_raw.get("as_of")},
            )
        )

    if ranked:
        top = ranked[0]
        rng = merged_prediction.get("range") or {}
        for row in ranked[:5]:
            action = str(row.get("action", "BUY")).upper()
            if action == "HOLD":
                continue
            target_px = row.get("target") or merged_prediction.get("target")
            stop_px = row.get("stop") or merged_prediction.get("stop")
            if action == "BUY" and spot > 0:
                if target_px is None or float(target_px) <= spot:
                    target_px = rng.get("high") or row.get("target")
                if stop_px is None or float(stop_px) >= spot:
                    stop_px = rng.get("low") or row.get("stop")
            row["target"] = target_px
            row["stop"] = stop_px
            legs = [
                {
                    "symbol": sym,
                    "side": action,
                    "price": spot,
                    "quantity": row.get("quantity", 1),
                    "product": row.get("product", "CNC"),
                }
            ]
            row_charges = calculate_equity_charges(legs, product=row.get("product", "CNC"))
            entry_charges = float((row_charges.get("total") or {}).get("total_charges") or 0)
            exit_charges = float(row_charges.get("exit_charges") or 0)
            row_payoff = build_stock_payoff(
                spot,
                int(row.get("quantity", 1)),
                target=target_px,
                stop=stop_px,
                entry_charges=entry_charges,
                exit_charges=exit_charges,
            )
            row["legs"] = legs
            row["charges"] = row_charges
            row["payoff"] = row_payoff
            row["max_profit"] = row_payoff.get("max_profit")
            row["max_loss"] = row_payoff.get("max_loss")
            row["net_max_profit"] = row_payoff.get("net_max_profit")
            row["net_max_loss"] = row_payoff.get("net_max_loss")

        top = ranked[0]
        legs = top.get("legs") or [
            {
                "symbol": sym,
                "side": top.get("action", "BUY"),
                "price": spot,
                "quantity": top.get("quantity", 1),
                "product": top.get("product", "CNC"),
            }
        ]
        doc.recommended = dict(top)
        doc.charges = top.get("charges") or calculate_equity_charges(legs, product=top.get("product", "CNC"))
        doc.payoff = top.get("payoff") or {}
        target_px = top.get("target") or merged_prediction.get("target")
        stop_px = top.get("stop") or merged_prediction.get("stop")
        doc.recommended["max_profit"] = doc.payoff.get("max_profit")
        doc.recommended["max_loss"] = doc.payoff.get("max_loss")
        doc.recommended["net_max_profit"] = doc.payoff.get("net_max_profit")
        doc.recommended["net_max_loss"] = doc.payoff.get("net_max_loss")
        doc.recommended["target"] = target_px
        doc.recommended["stop"] = stop_px
        doc.recommended["legs"] = legs
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
