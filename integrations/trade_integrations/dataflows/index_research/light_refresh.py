"""Lightweight index prediction refresh (macro + cached constituents)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from trade_integrations.context.hub import load_index_research_json, save_index_research
from trade_integrations.dataflows.company_research.models import StageResult
from trade_integrations.dataflows.index_research.attribution import (
    attribute_constituents,
    rollup_attribution,
)
from trade_integrations.dataflows.index_research.constituent_momentum import (
    attach_constituent_momentum,
    momentum_coverage_stats,
    rollup_constituent_momentum,
)
from trade_integrations.dataflows.index_research.explain import build_factor_explanation_bundle
from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.macro_global import fetch_global_macro_snapshot
from trade_integrations.dataflows.index_research.factor_store import upsert_daily_factors
from trade_integrations.dataflows.index_research.models import ConstituentSignal, IndexResearchDoc, PredictionRecord
from trade_integrations.dataflows.index_research.prediction_ledger import (
    append_prediction,
    build_prediction_metadata,
)
from trade_integrations.dataflows.index_research.predictor import load_stored_model_artifact, predict_nifty
from trade_integrations.dataflows.index_research.regime import classify_regime
from trade_integrations.dataflows.index_research.scenarios import (
    build_index_scenarios,
    reconcile_prediction_with_scenarios,
)
from trade_integrations.dataflows.index_research.sources.batch_constituents import (
    batch_constituent_research,
)

logger = logging.getLogger(__name__)

_MACRO_DRIFT_ENV = "INDEX_MONITOR_MACRO_DRIFT_PCT"
_DEFAULT_MACRO_DRIFT_PCT = 0.5


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_spot(ticker: str) -> float:
    from trade_integrations.dataflows.index_research.aggregator import _fetch_spot as agg_fetch

    return agg_fetch(ticker)


def _nifty_trend_20d() -> str:
    from trade_integrations.dataflows.index_research.aggregator import _nifty_trend_20d as agg_trend

    return agg_trend()


def _sector_breadth(signals: list[ConstituentSignal]) -> dict:
    from trade_integrations.dataflows.index_research.aggregator import _sector_breadth as agg_breadth

    return agg_breadth(signals)


def _macro_drift_threshold() -> float:
    try:
        return float(os.getenv(_MACRO_DRIFT_ENV, str(_DEFAULT_MACRO_DRIFT_PCT)))
    except ValueError:
        return _DEFAULT_MACRO_DRIFT_PCT


def _macro_factor_changed(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    threshold_pct: float,
) -> bool:
    for key, new_val in current.items():
        if isinstance(new_val, (dict, list, tuple, set)):
            continue
        try:
            new_f = float(new_val)
            old_f = float(previous.get(key, new_f))
        except (TypeError, ValueError):
            continue
        if old_f == 0:
            if abs(new_f) > 0.01:
                return True
            continue
        if abs((new_f - old_f) / old_f * 100.0) >= threshold_pct:
            return True
    return False


def _news_since_for_index(ticker: str) -> datetime:
    doc = load_index_research_json(ticker)
    if doc is None:
        return _stage_now()
    as_of = doc.as_of
    if hasattr(as_of, "tzinfo") and as_of.tzinfo is None:
        return as_of.replace(tzinfo=timezone.utc)
    return as_of if isinstance(as_of, datetime) else _stage_now()


def _material_news_for_index(ticker: str) -> list[str]:
    try:
        from trade_integrations.monitor.news_watcher import check_material_news
    except ImportError:
        return []
    since = _news_since_for_index(ticker)
    return check_material_news(ticker, since)


def _heavyweight_news(signals: list[ConstituentSignal]) -> bool:
    headlines = _material_news_for_index("NIFTY")
    if not headlines:
        return False
    top_symbols = {signal.symbol for signal in signals[:10]}
    joined = " ".join(headlines).upper()
    return any(sym in joined for sym in top_symbols)


def run_index_light_refresh(
    ticker: str = "NIFTY",
    *,
    horizon_days: int | None = None,
    force: bool = False,
) -> tuple[IndexResearchDoc, str]:
    """Recompute prediction using cached constituents and fresh macro factors."""
    sym = ticker.strip().upper()
    horizon = resolve_horizon(horizon_days)
    cached_doc = load_index_research_json(sym)

    previous_factors: dict[str, Any] = {}
    if cached_doc and cached_doc.global_factors:
        for row in cached_doc.global_factors:
            if isinstance(row, dict) and row.get("factor") is not None:
                previous_factors[str(row["factor"])] = row.get("value")

    signals = batch_constituent_research(
        lookahead_days=horizon.days,
        refresh=False,
    )
    signals = attach_constituent_momentum(signals)
    momentum_count = sum(1 for s in signals if s.momentum_7d_pct is not None)
    sentiments = [s.sentiment_score for s in signals if s.sentiment_score is not None]
    macro_stage = fetch_global_macro_snapshot(constituent_sentiments=sentiments or None)
    macro_factors = dict(macro_stage.data.get("factors") or {})
    global_factors = list(macro_stage.data.get("factor_rows") or [])

    try:
        from datetime import date as _date

        from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
            merge_flow_derivatives_frame,
            upsert_flow_cash_cache,
        )

        today = _date.today().isoformat()
        flow = merge_flow_derivatives_frame(today, today)
        if not flow.empty:
            upsert_flow_cash_cache(flow.to_dict("records"))
        flow_rows = [
            {"factor": str(row["factor"]), "value": float(row["value"]), "source": row.get("source")}
            for row in global_factors
            if row.get("factor") is not None and row.get("value") is not None
        ]
        if flow_rows:
            upsert_daily_factors(today, flow_rows)
    except Exception as exc:
        logger.debug("light_refresh factor upsert skipped: %s", exc)

    momentum_rollup = rollup_constituent_momentum(signals)
    if momentum_rollup is not None:
        macro_factors["constituent_momentum_7d"] = momentum_rollup
        global_factors.append(
            {
                "factor": "constituent_momentum_7d",
                "value": momentum_rollup,
                "source": "constituent_momentum",
            }
        )

    headlines = _material_news_for_index(sym)
    macro_changed = _macro_factor_changed(
        previous_factors,
        macro_factors,
        threshold_pct=_macro_drift_threshold(),
    )
    news_hit = bool(headlines) or _heavyweight_news(signals)

    if not force and cached_doc is not None and not macro_changed and not news_hit:
        return cached_doc, "unchanged"

    reason = "material_news" if news_hit else "macro_drift" if macro_changed else "forced"

    spot = _fetch_spot(sym)
    regime = classify_regime(
        india_vix=macro_factors.get("india_vix"),
        nifty_trend_20d=_nifty_trend_20d(),
    )
    prediction = (
        predict_nifty(
            spot=spot,
            signals=signals,
            macro_factors=macro_factors,
            horizon=horizon,
        )
        if spot > 0
        else {}
    )
    attributed = attribute_constituents(signals, horizon_days=horizon.days)
    rollup = rollup_attribution(attributed)
    if prediction:
        prediction["top_drivers"] = rollup.get("top_drivers", [])[:5]
        prediction["momentum_coverage"] = momentum_coverage_stats(signals)

    scenarios = (
        build_index_scenarios(
            signals,
            macro_factors,
            spot=spot,
            horizon_days=horizon.days,
        )
        if spot > 0
        else []
    )

    if spot > 0 and prediction and scenarios:
        artifact = load_stored_model_artifact()
        mae_pct = float(artifact.mae if artifact else 1.5)
        prediction = reconcile_prediction_with_scenarios(
            prediction,
            scenarios,
            spot=spot,
            mae_pct=mae_pct,
        )

    factor_bundle: dict[str, Any] = {}
    if spot > 0 and prediction:
        factor_bundle = build_factor_explanation_bundle(
            macro_factors,
            scenarios,
            horizon=horizon,
            spot=spot,
            bottom_up_return_pct=float(prediction.get("bottom_up_return_pct") or 0.0),
            headline_return_pct=float(prediction.get("expected_return_pct") or 0.0),
        )
        prediction["factor_contributors"] = factor_bundle.get("factor_explanation", {}).get(
            "contributors", []
        )

    stages: list[StageResult] = [
        StageResult(
            stage="constituents",
            status="ok" if signals else "partial",
            vendor="batch_constituents_cached",
            fetched_at=_stage_now(),
            data={"count": len(signals), "refresh": False, "momentum_count": momentum_count},
        ),
        macro_stage,
    ]

    if spot > 0 and prediction:
        expected = float(prediction.get("expected_return_pct") or 0.0)
        range_block = prediction.get("range") or {}
        append_prediction(
            PredictionRecord(
                predicted_at=_stage_now(),
                horizon_days=horizon.days,
                spot_at_prediction=spot,
                expected_return_pct=expected,
                range_low=float(range_block.get("low") or spot),
                range_high=float(range_block.get("high") or spot),
                metadata=build_prediction_metadata(
                    ticker=sym,
                    horizon_name=horizon.name,
                    refresh="light",
                    prediction=prediction,
                    global_factors=global_factors,
                    regime=regime,
                    scenarios=scenarios,
                ),
            )
        )

    doc = IndexResearchDoc(
        ticker=sym,
        as_of=_stage_now(),
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
                "momentum_7d_pct": signal.momentum_7d_pct,
                "contribution_to_index_pct": signal.contribution_to_index_pct,
                "events": signal.events,
                "factors": signal.factors,
            }
            for signal in attributed
        ],
        sector_breadth=_sector_breadth(signals),
        scenarios=scenarios,
        accuracy=cached_doc.accuracy if cached_doc else {},
        factor_explanation=factor_bundle.get("factor_explanation") or {},
        factor_sensitivity=factor_bundle.get("factor_sensitivity") or [],
        event_impact_curves=factor_bundle.get("event_impact_curves") or [],
        stages=stages,
    )
    save_index_research(doc)
    return doc, reason
