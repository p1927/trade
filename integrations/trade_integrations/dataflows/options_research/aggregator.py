"""Pipeline orchestrator for options research."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from .browse_summary import build_browse_summary
from .candidate_generator import generate_candidates
from .config import get_options_config
from trade_integrations.dataflows.company_research.market import Market
from .market import (
    InstrumentType,
    options_research_ineligible_reason,
    resolve_options_instrument,
)
from .models import OptionsResearchDoc
from .payoff_charges import build_implementation_steps
from .strategy_ranker import build_scenarios, rank_strategies
from trade_integrations.dataflows.company_research.models import StageResult
from .sources.analytics_history import fetch_analytics_history
from .sources.analytics_qfin import fetch_analytics_qfin, simple_analytics_fallback
from .sources.chain_openalgo import fetch_chain_stage
from .sources.events_index import fetch_events_index
from .sources.events_stock import fetch_events_stock
from .sources.earnings_us import fetch_earnings_us_stage

logger = logging.getLogger(__name__)


def _strategy_builder_base() -> str:
    host = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5001").rstrip("/")
    return f"{host}/strategybuilder"


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
    if result.stage == "earnings_us" and result.data and result.status == "ok":
        doc.prediction.setdefault("earnings", {}).update(result.data)


def _prediction_view(
    analytics: dict,
    events: list,
    *,
    prediction_signals: dict | None = None,
) -> str:
    signals = prediction_signals or {}
    bias = signals.get("earnings_bias")
    if bias == "bullish":
        return "bullish_earnings"
    if bias == "bearish":
        return "bearish_earnings"
    if signals.get("corp_event_score") is not None and float(signals["corp_event_score"]) >= 50:
        return "corp_event_vol"
    if analytics.get("bias"):
        return str(analytics["bias"])
    iv_regime = analytics.get("iv_regime", "moderate")
    if events and any(e.get("impact_on_vol") in ("elevated", "high") for e in events):
        return "event_volatility"
    if iv_regime == "high":
        return "range_short_vol"
    if iv_regime == "low":
        return "directional_debit"
    return "neutral"


def _skipped_options_doc(
    ticker: str,
    *,
    reason: str,
    now: datetime,
    days: int,
) -> OptionsResearchDoc:
    """Return a minimal hub doc when India options research does not apply."""
    instrument = resolve_options_instrument(ticker)
    doc = OptionsResearchDoc(
        underlying=instrument.display_symbol,
        as_of=now,
        lookahead_days=days,
        instrument_type=instrument.instrument_type.value,
        market=instrument.market.value,
        execution_market=instrument.market.value,
        meta={
            "input_ticker": instrument.input_ticker,
            "underlying_symbol": instrument.underlying_symbol,
            "underlying_exchange": instrument.underlying_exchange,
            "options_exchange": instrument.options_exchange,
            "skip_reason": reason,
        },
    )
    doc.stages.append(
        StageResult(
            stage="market",
            status="skipped",
            vendor="trade_integrations.options_market",
            fetched_at=now,
            data={"reason": reason, "eligible": False},
            errors=[reason],
        )
    )
    return doc


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
    skip_reason = options_research_ineligible_reason(ticker)
    if skip_reason:
        return _skipped_options_doc(ticker, reason=skip_reason, now=now, days=days)
    instrument = resolve_options_instrument(ticker)

    doc = OptionsResearchDoc(
        underlying=instrument.display_symbol,
        as_of=now,
        lookahead_days=days,
        instrument_type=instrument.instrument_type.value,
        market=instrument.market.value,
        execution_market=instrument.market.value,
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
    if (doc.spot is None or float(doc.spot or 0) <= 0) and chain_result.status == "error":
        try:
            from trade_integrations.monitor.live_quotes import fetch_underlying_ltp

            fallback_spot = fetch_underlying_ltp(instrument.display_symbol)
            if fallback_spot is not None and float(fallback_spot) > 0:
                doc.spot = float(fallback_spot)
                doc.meta.setdefault("spot_provenance", "quote_fallback")
        except Exception as exc:
            logger.warning(
                "spot fallback failed for %s: %s",
                instrument.display_symbol,
                exc,
            )
            doc.stages.append(
                StageResult(
                    stage="spot_fallback",
                    status="error",
                    vendor="live_quotes",
                    fetched_at=now,
                    errors=[str(exc)],
                )
            )
    doc.browse_summary = build_browse_summary(doc.chain_snapshot)

    prediction_signals: dict = {}
    if instrument.instrument_type == InstrumentType.STOCK:
        events_result = fetch_events_stock(instrument, lookahead_days=days)
        _apply_stage(doc, events_result)
        prediction_signals = (events_result.data or {}).get("prediction_signals") or {}
        if events_result.data:
            if events_result.data.get("earnings_signal"):
                doc.prediction.setdefault("earnings", {}).update(
                    events_result.data["earnings_signal"]
                )
            if events_result.data.get("corp_events"):
                doc.prediction.setdefault("corp_events", {}).update(
                    events_result.data["corp_events"]
                )
            if prediction_signals:
                doc.prediction.setdefault("signals", {}).update(prediction_signals)
        if instrument.market == Market.US:
            _apply_stage(doc, fetch_earnings_us_stage(instrument.input_ticker))
    else:
        _apply_stage(doc, fetch_events_index(lookahead_days=days))

    analytics_result = fetch_analytics_qfin(doc.chain_snapshot)
    _apply_stage(doc, analytics_result)
    if analytics_result.status in ("skipped", "error"):
        reason = (analytics_result.data or {}).get("reason", "")
        errors = analytics_result.errors or []
        if "qfinindia" in reason.lower() or any("qfinindia" in e.lower() for e in errors):
            doc.prediction["analytics_hint"] = "pip install qfinindia (or fix numpy compat: trapz)"
        elif analytics_result.status == "error":
            doc.prediction["analytics_hint"] = f"qfinindia failed: {(errors or ['unknown'])[0][:80]}"
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
        prediction_signals=prediction_signals,
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
            "view": _prediction_view(
                doc.prediction,
                doc.events,
                prediction_signals=prediction_signals,
            ),
            "expected_move_pct": doc.prediction.get("expected_move_pct")
            or doc.prediction.get("expected_move"),
            "iv_regime": iv_regime,
            "confidence": round(ranked[0].get("score", 0), 2) if ranked else 0.0,
        }
    )

    from trade_integrations.context.hub import load_agent_debate_json
    from trade_integrations.research.debate_synthesis import (
        extract_structured_debate,
        merge_options_context,
    )

    debate_raw = load_agent_debate_json(instrument.display_symbol)
    debate_struct = extract_structured_debate(debate_raw)
    if debate_struct and ranked:
        merged_ctx = merge_options_context(debate_struct, doc)
        ranked = merged_ctx.get("ranked_strategies") or ranked
        doc.ranked_strategies = ranked
        if merged_ctx.get("prediction"):
            doc.prediction.update(merged_ctx["prediction"])
        if debate_raw:
            doc.stages.append(
                StageResult(
                    stage="debate_synthesis",
                    status="ok",
                    vendor="agent_debate",
                    fetched_at=now,
                    data={"debate_as_of": debate_raw.get("as_of"), "view": debate_struct.get("view")},
                )
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
        sb = _strategy_builder_base()
        doc.meta["strategy_builder_url"] = f"{sb}?plan={sym}"
        doc.meta["strategy_builder_pnl_url"] = f"{sb}?plan={sym}&tab=pnl"
        doc.meta["strategy_builder_execute_url"] = f"{sb}?plan={sym}&execute=1"

    doc.stages.append(
        StageResult(
            stage="payoff",
            status="ok" if doc.payoff else "skipped",
            vendor="payoff_charges",
            fetched_at=now,
            data={"has_payoff": bool(doc.payoff), "has_charges": bool(doc.charges)},
        )
    )

    if ranked and doc.recommended.get("name") and spot > 0 and doc.expiry:
        try:
            from trade_integrations.dataflows.options_research.prediction_ledger import (
                OptionsPredictionRecord,
                append_options_prediction,
            )

            expected_move = float(
                doc.prediction.get("expected_move_pct")
                or doc.prediction.get("expected_move")
                or 0.0
            )
            append_options_prediction(
                OptionsPredictionRecord(
                    underlying=doc.underlying,
                    predicted_at=now,
                    expiry_date=str(doc.expiry),
                    spot_at_prediction=spot,
                    prediction_view=str(doc.prediction.get("view") or "neutral"),
                    expected_move_pct=expected_move,
                    strategy_name=str(doc.recommended.get("name") or ""),
                    strategy_score=float(doc.recommended.get("score") or ranked[0].get("score") or 0.0),
                    metadata={
                        "instrument_type": doc.instrument_type,
                        "iv_regime": iv_regime,
                        "tier": doc.recommended.get("tier"),
                    },
                )
            )
        except Exception as exc:
            logger.warning(
                "options prediction ledger append failed for %s: %s",
                doc.underlying,
                exc,
            )

    return doc
