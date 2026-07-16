"""Pipeline orchestrator for Nifty index research."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.company_research.models import StageResult
from trade_integrations.dataflows.index_research.constituent_momentum import (
    attach_constituent_momentum,
    momentum_coverage_stats,
    rollup_constituent_momentum,
)
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
    build_prediction_metadata,
    compute_accuracy_metrics,
)
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger
from trade_integrations.dataflows.index_research.predictor import load_stored_model_artifact, predict_nifty
from trade_integrations.dataflows.index_research.regime import classify_regime
from trade_integrations.dataflows.index_research.scenarios import (
    build_index_scenarios,
    reconcile_prediction_with_scenarios,
)
from trade_integrations.dataflows.index_research.sources.batch_constituents import (
    batch_constituent_research,
)
from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history
from trade_integrations.dataflows.index_research.upcoming_events import build_upcoming_events

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
    pipeline: PipelineLogger | None = None,
) -> IndexResearchDoc:
    """Build a full index research dossier with prediction, scenarios, and ledger entry."""
    log = pipeline or PipelineLogger()
    now = _stage_now()
    sym = ticker.strip().upper()
    horizon = resolve_horizon(horizon_days)
    stages: list[StageResult] = []

    log.info(
        "start",
        f"Starting NIFTY 50 analysis — horizon {horizon.days}d (profile {horizon.name})",
        ticker=sym,
        refresh_constituents=refresh_constituents,
    )

    log.info(
        "constituents",
        "Loading NIFTY 50 constituent research (news, sentiment, calendar, filings)…",
    )
    signals = batch_constituent_research(
        lookahead_days=horizon.days,
        refresh=refresh_constituents,
    )
    with_news = sum(
        1 for s in signals if any(f.get("type") == "news" for f in (s.factors or []))
    )
    with_sentiment = sum(1 for s in signals if s.sentiment_score is not None)
    log.info(
        "constituents",
        f"Loaded {len(signals)} signals — sentiment {with_sentiment}, news factors {with_news}",
        count=len(signals),
        with_sentiment=with_sentiment,
        with_news=with_news,
    )

    log.info("momentum", "Fetching 7-day price momentum per constituent…")
    signals = attach_constituent_momentum(signals)
    momentum_count = sum(1 for s in signals if s.momentum_7d_pct is not None)
    log.info(
        "momentum",
        f"Momentum attached for {momentum_count}/{len(signals)} stocks",
        with_momentum=momentum_count,
    )
    stages.append(
        StageResult(
            stage="constituents",
            status="ok" if signals else "partial",
            vendor="batch_constituents",
            fetched_at=now,
            data={"count": len(signals), "momentum_count": momentum_count},
            errors=[] if signals else ["no constituent signals"],
        )
    )

    log.info("macro", "Collecting global macro, technical, calendar, and PCR factors…")
    sentiments = [signal.sentiment_score for signal in signals if signal.sentiment_score is not None]
    macro_stage = fetch_global_macro_snapshot(
        constituent_sentiments=sentiments or None,
    )
    stages.append(macro_stage)
    macro_factors = dict(macro_stage.data.get("factors") or {})
    factor_names = sorted(macro_factors.keys())
    log.info(
        "macro",
        f"Macro snapshot: {len(factor_names)} factors",
        factors=factor_names,
        status=macro_stage.status,
    )
    for err in macro_stage.errors or []:
        log.warn("macro", err)

    momentum_rollup = rollup_constituent_momentum(signals)
    if momentum_rollup is not None:
        macro_factors["constituent_momentum_7d"] = momentum_rollup
        log.info(
            "momentum",
            f"Weighted constituent momentum 7d: {momentum_rollup:.2f}%",
            value=momentum_rollup,
        )
    global_factors = list(macro_stage.data.get("factor_rows") or [])
    if momentum_rollup is not None:
        global_factors.append(
            {
                "factor": "constituent_momentum_7d",
                "value": momentum_rollup,
                "source": "constituent_momentum",
            }
        )

    log.info("spot", f"Fetching live NIFTY spot via OpenAlgo / yfinance…")
    spot = _fetch_spot(sym)
    log.info("spot", f"Spot: {spot:.2f}" if spot > 0 else "Spot unavailable", spot=spot)

    trend = _nifty_trend_20d()
    regime = classify_regime(
        india_vix=macro_factors.get("india_vix"),
        nifty_trend_20d=trend,
    )
    log.info(
        "regime",
        f"Regime: {regime.get('label', 'unknown')} (20d trend: {trend})",
        regime=regime.get("label"),
        trend_20d=trend,
    )

    log.info("predict", "Running hybrid predictor (bottom-up + macro Ridge + direction head)…")
    prediction = predict_nifty(
        spot=spot,
        signals=signals,
        macro_factors=macro_factors,
        horizon=horizon,
    ) if spot > 0 else {}
    if prediction:
        prediction["momentum_coverage"] = momentum_coverage_stats(signals)
        log.info(
            "predict",
            f"Forecast: {prediction.get('view')} {prediction.get('expected_return_pct'):+.2f}% "
            f"(bottom-up {prediction.get('bottom_up_return_pct'):+.2f}%, "
            f"macro Δ {prediction.get('macro_delta_pct'):+.2f}%)",
            view=prediction.get("view"),
            direction_view=prediction.get("direction_view"),
            expected_return_pct=prediction.get("expected_return_pct"),
        )

    log.info("attribution", "Attributing constituent contributions to index…")
    attributed = attribute_constituents(signals, horizon_days=horizon.days)
    rollup = rollup_attribution(attributed)
    prediction["top_drivers"] = rollup.get("top_drivers", [])[:5]
    if rollup.get("top_drivers"):
        top = rollup["top_drivers"][0]
        log.info(
            "attribution",
            f"Top driver: {top.get('symbol')} ({top.get('contribution_to_index_pct'):+.2f}%)",
            top_drivers=rollup.get("top_drivers", [])[:3],
        )

    log.info("scenarios", "Building event scenarios (earnings, RBI, expiry, budget)…")
    scenarios = build_index_scenarios(
        signals,
        macro_factors,
        spot=spot,
        horizon_days=horizon.days,
    ) if spot > 0 else []
    log.info("scenarios", f"{len(scenarios)} scenarios built", events=[s.get("event") for s in scenarios])

    if spot > 0 and prediction and scenarios:
        artifact = load_stored_model_artifact()
        mae_pct = float(artifact.mae if artifact else 1.5)
        before = float(prediction.get("expected_return_pct") or 0.0)
        prediction = reconcile_prediction_with_scenarios(
            prediction,
            scenarios,
            spot=spot,
            mae_pct=mae_pct,
        )
        after = float(prediction.get("expected_return_pct") or 0.0)
        if prediction.get("reconciled_with_scenarios"):
            log.info(
                "reconcile",
                f"Reconciled forecast {before:+.2f}% → {after:+.2f}% toward scenarios",
                before=before,
                after=after,
            )

    from trade_integrations.context.hub import load_agent_debate_json
    from trade_integrations.research.debate_synthesis import (
        extract_structured_debate,
        merge_index_prediction,
    )

    debate_raw = load_agent_debate_json(sym)
    debate_struct = extract_structured_debate(debate_raw)
    if debate_struct and prediction:
        prediction = merge_index_prediction(debate_struct, prediction)
        stages.append(
            StageResult(
                stage="debate_synthesis",
                status="ok",
                vendor="agent_debate",
                fetched_at=now,
                data={"debate_as_of": debate_raw.get("as_of"), "view": debate_struct.get("view")},
            )
        )
        log.info(
            "debate",
            f"Merged agent debate view: {debate_struct.get('view')}",
            debate_view=debate_struct.get("view"),
        )

    factor_bundle: dict[str, Any] = {}
    if spot > 0 and prediction:
        log.info("explain", "Building factor explanation and sensitivity…")
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
        contributors = prediction.get("factor_contributors") or []
        log.info("explain", f"{len(contributors)} factor contributors ranked")

    log.info("accuracy", "Computing prediction ledger accuracy metrics…")
    accuracy = compute_accuracy_metrics()
    if accuracy.get("sample_count"):
        log.info(
            "accuracy",
            f"Ledger: {accuracy.get('sample_count')} forecasts, "
            f"direction hit {accuracy.get('direction_hit_rate_14d') or accuracy.get('direction_hit_rate')}",
            **{k: accuracy[k] for k in ("sample_count", "mae_pct", "direction_hit_rate_14d") if k in accuracy},
        )

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
                metadata=build_prediction_metadata(
                    ticker=sym,
                    horizon_name=horizon.name,
                    refresh="full",
                    prediction=prediction,
                    global_factors=global_factors,
                    regime=regime,
                    scenarios=scenarios,
                ),
            )
        )
        log.info("ledger", "Appended forecast to prediction ledger")

    log.info("done", "Pipeline complete — saving hub artifact")
    upcoming_events = build_upcoming_events(
        signals,
        macro_factors,
        horizon_days=horizon.days,
    )
    if upcoming_events:
        log.info(
            "events",
            f"{len(upcoming_events)} upcoming events in {horizon.days}d horizon",
            count=len(upcoming_events),
        )

    news_impact: dict[str, Any] = {}
    try:
        from trade_integrations.dataflows.index_research.news_impact_engine import (
            build_news_impact_snapshot,
            save_news_impact_snapshot,
        )

        macro_map = {
            str(r.get("factor")): float(r.get("value"))
            for r in global_factors
            if r.get("factor") is not None and r.get("value") is not None
        }
        news_impact = build_news_impact_snapshot(
            ticker=sym,
            horizon_days=horizon.days,
            spot=float(spot or 0),
            macro_factors=macro_map,
        )
        save_news_impact_snapshot(news_impact, ticker=sym)
        stages.append(
            StageResult(
                stage="news_impact",
                status="ok",
                vendor="news_verification",
                fetched_at=now,
                data={
                    "approved": (news_impact.get("summary") or {}).get("approved_count"),
                    "items": len(news_impact.get("items") or []),
                },
            )
        )
        log.info(
            "news_impact",
            f"{len(news_impact.get('items') or [])} verified headlines",
            skipped=(news_impact.get("summary") or {}).get("rejected_skipped"),
        )
    except Exception as exc:
        log.info("news_impact", f"skipped: {exc}")

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
                "momentum_7d_pct": signal.momentum_7d_pct,
                "contribution_to_index_pct": signal.contribution_to_index_pct,
                "events": signal.events,
                "factors": signal.factors,
            }
            for signal in attributed
        ],
        sector_breadth=_sector_breadth(signals),
        scenarios=scenarios,
        accuracy=accuracy,
        factor_explanation=factor_bundle.get("factor_explanation") or {},
        factor_sensitivity=factor_bundle.get("factor_sensitivity") or [],
        event_impact_curves=factor_bundle.get("event_impact_curves") or [],
        upcoming_events=upcoming_events,
        news_impact=news_impact,
        stages=stages,
        pipeline_log=log.to_dicts(),
    )
