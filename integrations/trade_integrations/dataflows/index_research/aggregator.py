"""Pipeline orchestrator for Nifty index research."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.company_research.models import StageResult
from trade_integrations.dataflows.index_research.attribution import (
    attribute_constituents,
    rollup_attribution,
)
from trade_integrations.dataflows.index_research.explain import build_factor_explanation_bundle
from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.macro_global import fetch_global_macro_snapshot
from trade_integrations.dataflows.index_research.models import ConstituentSignal, IndexResearchDoc, PredictionRecord
from trade_integrations.dataflows.index_research.prediction_ledger import (
    append_prediction,
    compute_accuracy_metrics,
)
from trade_integrations.dataflows.index_research.predictor import predict_nifty
from trade_integrations.dataflows.index_research.regime import classify_regime
from trade_integrations.dataflows.index_research.scenarios import build_index_scenarios
from trade_integrations.dataflows.index_research.sources.batch_constituents import (
    batch_constituent_research,
)
from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_spot(ticker: str) -> float:
    from trade_integrations.dataflows.openalgo import fetch_openalgo_quote

    try:
        quote = fetch_openalgo_quote(ticker)
        if quote and quote.get("ltp"):
            return float(quote["ltp"])
    except Exception as exc:
        logger.debug("OpenAlgo spot fetch failed for %s: %s", ticker, exc)

    hist = load_nifty_history(days=10)
    if not hist.empty:
        return float(hist["close"].iloc[-1])
    return 0.0


def _nifty_trend_20d() -> str:
    hist = load_nifty_history(days=35)
    if len(hist) < 21:
        return "sideways"
    close_now = float(hist["close"].iloc[-1])
    close_20d = float(hist["close"].iloc[-21])
    if close_20d <= 0:
        return "sideways"
    pct = (close_now - close_20d) / close_20d * 100.0
    if pct > 2.0:
        return "up"
    if pct < -2.0:
        return "down"
    return "sideways"


def _sector_breadth(signals: list[ConstituentSignal]) -> dict:
    by_sector: dict[str, list[float]] = defaultdict(list)
    for signal in signals:
        if signal.sentiment_score is None or not signal.sector:
            continue
        by_sector[signal.sector].append(signal.sentiment_score)

    sector_means = {
        sector: sum(scores) / len(scores)
        for sector, scores in by_sector.items()
        if scores
    }
    overall = (
        sum(sector_means.values()) / len(sector_means)
        if sector_means
        else None
    )
    return {
        "mean_sentiment": overall,
        "by_sector": sector_means,
        "sector_count": len(sector_means),
    }


def run_index_research(
    ticker: str = "NIFTY",
    *,
    horizon_days: int | None = None,
    refresh_constituents: bool = False,
) -> IndexResearchDoc:
    """Build a full index research dossier with prediction, scenarios, and ledger entry."""
    now = _stage_now()
    sym = ticker.strip().upper()
    horizon = resolve_horizon(horizon_days)
    stages: list[StageResult] = []

    signals = batch_constituent_research(
        lookahead_days=horizon.days,
        refresh=refresh_constituents,
    )
    stages.append(
        StageResult(
            stage="constituents",
            status="ok" if signals else "partial",
            vendor="batch_constituents",
            fetched_at=now,
            data={"count": len(signals)},
            errors=[] if signals else ["no constituent signals"],
        )
    )

    sentiments = [signal.sentiment_score for signal in signals if signal.sentiment_score is not None]
    macro_stage = fetch_global_macro_snapshot(
        constituent_sentiments=sentiments or None,
    )
    stages.append(macro_stage)
    macro_factors = dict(macro_stage.data.get("factors") or {})
    global_factors = list(macro_stage.data.get("factor_rows") or [])

    spot = _fetch_spot(sym)
    regime = classify_regime(
        india_vix=macro_factors.get("india_vix"),
        nifty_trend_20d=_nifty_trend_20d(),
    )

    prediction = predict_nifty(
        spot=spot,
        signals=signals,
        macro_factors=macro_factors,
        horizon=horizon,
    ) if spot > 0 else {}

    attributed = attribute_constituents(signals, horizon_days=horizon.days)
    rollup = rollup_attribution(attributed)
    prediction["top_drivers"] = rollup.get("top_drivers", [])[:5]

    scenarios = build_index_scenarios(
        signals,
        macro_factors,
        spot=spot,
        horizon_days=horizon.days,
    ) if spot > 0 else []

    factor_bundle: dict[str, Any] = {}
    if spot > 0 and prediction:
        factor_bundle = build_factor_explanation_bundle(
            macro_factors,
            scenarios,
            horizon=horizon,
            spot=spot,
            bottom_up_return_pct=float(prediction.get("bottom_up_return_pct") or 0.0),
        )
        prediction["factor_contributors"] = factor_bundle.get("factor_explanation", {}).get(
            "contributors", []
        )

    accuracy = compute_accuracy_metrics()

    if spot > 0 and prediction:
        expected = float(prediction.get("expected_return_pct") or 0.0)
        range_block = prediction.get("range") or {}
        append_prediction(
            PredictionRecord(
                predicted_at=now,
                horizon_days=horizon.days,
                spot_at_prediction=spot,
                expected_return_pct=expected,
                range_low=float(range_block.get("low") or spot),
                range_high=float(range_block.get("high") or spot),
                metadata={"ticker": sym, "horizon_name": horizon.name},
            )
        )

    return IndexResearchDoc(
        ticker=sym,
        as_of=now,
        horizon={"name": horizon.name, "days": horizon.days},
        spot=spot or None,
        prediction=prediction,
        regime=regime,
        global_factors=global_factors,
        constituent_signals=[
            {
                "symbol": signal.symbol,
                "weight": signal.weight,
                "sector": signal.sector,
                "sentiment_score": signal.sentiment_score,
                "contribution_to_index_pct": signal.contribution_to_index_pct,
                "events": signal.events,
            }
            for signal in attributed
        ],
        sector_breadth=_sector_breadth(signals),
        scenarios=scenarios,
        accuracy=accuracy,
        factor_explanation=factor_bundle.get("factor_explanation") or {},
        factor_sensitivity=factor_bundle.get("factor_sensitivity") or [],
        event_impact_curves=factor_bundle.get("event_impact_curves") or [],
        stages=stages,
    )
