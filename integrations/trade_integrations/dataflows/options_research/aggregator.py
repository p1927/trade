"""Pipeline orchestrator for options research."""

from __future__ import annotations

from datetime import datetime, timezone

from .browse_summary import build_browse_summary
from .candidate_generator import generate_candidates
from .config import get_options_config
from .market import InstrumentType, resolve_options_instrument
from .models import OptionsResearchDoc
from .payoff_charges import build_implementation_steps
from .strategy_ranker import build_scenarios, rank_strategies
from trade_integrations.dataflows.company_research.models import StageResult
from .sources.analytics_history import fetch_analytics_history
from .sources.analytics_qfin import fetch_analytics_qfin, simple_analytics_fallback
from .sources.chain_openalgo import fetch_chain_stage
from .sources.events_index import fetch_events_index
from .sources.events_stock import fetch_events_stock


def _apply_stage(doc: OptionsResearchDoc, result: StageResult) -> None:
    doc.stages.append(result)
    if result.stage == "chain" and result.data:
        doc.chain_snapshot = dict(result.data)
        doc.spot = result.data.get("underlying_ltp")
        doc.expiry = str(result.data.get("expiry_date") or doc.expiry)
    if result.stage == "events" and result.data:
        doc.events = list(result.data.get("events") or [])
    if result.stage == "analytics" and result.data:
        doc.prediction.update(result.data)
    if result.stage == "analytics_history" and result.data:
        doc.prediction.setdefault("history", {}).update(result.data)


def _prediction_view(analytics: dict, events: list) -> str:
    bias = analytics.get("bias")
    if bias:
        return str(bias)
    iv_regime = analytics.get("iv_regime", "moderate")
    if events and any(e.get("impact_on_vol") in ("elevated", "high") for e in events):
        return "event_volatility"
    if iv_regime == "high":
        return "range_short_vol"
    if iv_regime == "low":
        return "directional_debit"
    return "neutral"


def run_options_research(
    ticker: str,
    *,
    expiry_date: str | None = None,
    lookahead_days: int | None = None,
) -> OptionsResearchDoc:
    """Run the full options research pipeline for one underlying."""
    config = get_options_config()
    days = lookahead_days if lookahead_days is not None else config.lookahead_days
    now = datetime.now(timezone.utc)
    instrument = resolve_options_instrument(ticker)

    doc = OptionsResearchDoc(
        underlying=instrument.display_symbol,
        as_of=now,
        lookahead_days=days,
        instrument_type=instrument.instrument_type.value,
        market=instrument.market.value,
        meta={
            "input_ticker": instrument.input_ticker,
            "underlying_symbol": instrument.underlying_symbol,
            "underlying_exchange": instrument.underlying_exchange,
            "options_exchange": instrument.options_exchange,
        },
    )

    market_stage = StageResult(
        stage="market",
        status="ok",
        vendor="trade_integrations.options_market",
        fetched_at=now,
        data={
            "instrument_type": instrument.instrument_type.value,
            "underlying_exchange": instrument.underlying_exchange,
            "options_exchange": instrument.options_exchange,
        },
    )
    _apply_stage(doc, market_stage)

    chain_result = fetch_chain_stage(
        instrument,
        expiry_date=expiry_date,
        strike_count=config.strike_count,
    )
    _apply_stage(doc, chain_result)
    doc.browse_summary = build_browse_summary(doc.chain_snapshot)

    if instrument.instrument_type == InstrumentType.STOCK:
        _apply_stage(doc, fetch_events_stock(instrument, lookahead_days=days))
    else:
        _apply_stage(doc, fetch_events_index(lookahead_days=days))

    analytics_result = fetch_analytics_qfin(doc.chain_snapshot)
    _apply_stage(doc, analytics_result)
    if analytics_result.status in ("skipped", "error"):
        reason = (analytics_result.data or {}).get("reason", "")
        if "qfinindia" in reason.lower():
            doc.prediction["analytics_hint"] = "pip install -e '.[options]' for qfinindia analytics"
        fallback = simple_analytics_fallback(doc.chain_snapshot)
        doc.prediction.update(fallback)

    history_result = fetch_analytics_history(instrument)
    _apply_stage(doc, history_result)

    iv_regime = str(doc.prediction.get("iv_regime") or "moderate")
    has_event = any(
        e.get("type") not in ("india_vix", "macro_watch", "fii_dii_flow")
        for e in doc.events
    )
    candidates = generate_candidates(
        instrument,
        doc.chain_snapshot,
        iv_regime=iv_regime,
        has_event=has_event,
    )
    doc.stages.append(
        StageResult(
            stage="candidates",
            status="ok" if candidates else "partial",
            vendor="candidate_generator",
            fetched_at=now,
            data={"count": len(candidates), "names": [c.get("name") for c in candidates]},
        )
    )

    spot = float(doc.spot or doc.chain_snapshot.get("underlying_ltp") or 0)
    ranked = rank_strategies(
        candidates,
        chain_snapshot=doc.chain_snapshot,
        analytics=doc.prediction,
        history=doc.prediction.get("history") or history_result.data or {},
        events=doc.events,
        spot=spot,
        broker_preset=config.broker_preset,
    )
    doc.ranked_strategies = ranked
    doc.stages.append(
        StageResult(
            stage="rank",
            status="ok" if ranked else "partial",
            vendor="strategy_ranker",
            fetched_at=now,
            data={"top": ranked[0].get("name") if ranked else None},
        )
    )

    doc.scenarios = build_scenarios(doc.events, ranked)
    doc.prediction.update(
        {
            "view": _prediction_view(doc.prediction, doc.events),
            "expected_move_pct": doc.prediction.get("expected_move_pct")
            or doc.prediction.get("expected_move"),
            "iv_regime": iv_regime,
            "confidence": round(ranked[0].get("score", 0), 2) if ranked else 0.0,
        }
    )

    if ranked:
        top = ranked[0]
        doc.recommended = {
            "name": top.get("name"),
            "score": top.get("score"),
            "tier": top.get("tier"),
            "pop": top.get("pop"),
            "pop_source": top.get("pop_source"),
            "rationale": top.get("rationale"),
            "legs": top.get("legs") or [],
            "max_profit": top.get("max_profit"),
            "max_loss": top.get("max_loss"),
            "net_max_profit": top.get("net_max_profit"),
            "net_max_loss": top.get("net_max_loss"),
            "net_debit_credit": top.get("net_debit_credit"),
            "breakevens": top.get("breakevens"),
        }
        doc.payoff = top.get("payoff") or {}
        doc.payoff_over_time = top.get("payoff_over_time") or {}
        doc.charges = top.get("charges") or {}
        doc.implementation_steps = build_implementation_steps(
            doc.recommended,
            options_exchange=instrument.options_exchange,
        )
        sym = instrument.display_symbol
        doc.meta["strategy_builder_url"] = f"http://127.0.0.1:5000/strategybuilder?plan={sym}"
        doc.meta["strategy_builder_pnl_url"] = (
            f"http://127.0.0.1:5000/strategybuilder?plan={sym}&tab=pnl"
        )
        doc.meta["strategy_builder_execute_url"] = (
            f"http://127.0.0.1:5000/strategybuilder?plan={sym}&execute=1"
        )

    doc.stages.append(
        StageResult(
            stage="payoff",
            status="ok" if doc.payoff else "skipped",
            vendor="payoff_charges",
            fetched_at=now,
            data={"has_payoff": bool(doc.payoff), "has_charges": bool(doc.charges)},
        )
    )
    return doc
