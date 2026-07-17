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
    resolve_constituent_momentum_rollup,
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
from trade_integrations.dataflows.index_research.predictor import (
    finalize_index_prediction,
    load_stored_model_artifact,
    predict_nifty,
)
from trade_integrations.dataflows.index_research.regime import classify_regime
from trade_integrations.dataflows.index_research.scenarios import (
    build_index_scenarios,
    reconcile_prediction_with_scenarios,
    scenario_weighted_return_pct,
)
from trade_integrations.context.hub import load_index_research_json
from trade_integrations.dataflows.index_research.constituent_snapshot import signals_from_cached_doc
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


def _news_shock_summary() -> dict[str, Any]:
    try:
        from trade_integrations.dataflows.index_research.event_overlay import overlay_summary_for_ui

        return overlay_summary_for_ui("NIFTY")
    except Exception:
        return {}


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
        "data_completeness",
        "Checking flow-factor coverage (FII/DII/PCR gate)…",
    )
    completeness: dict[str, Any] = {"passes_gate": True, "after": {"min_pct": 100.0}}
    from trade_integrations.dataflows.index_research.data_completeness import (
        GATE_FAIL_MACRO_TRUST_MULTIPLIER,
        ensure_factor_data_complete,
    )
    try:
        completeness = ensure_factor_data_complete(
            enrich=refresh_constituents,
            allow_live_fetch=False,
        )
        log.info(
            "data_completeness",
            f"Flow coverage min {completeness.get('after', {}).get('min_pct')}% "
            f"(gate={'pass' if completeness.get('passes_gate') else 'fail'}"
            f"{'; cached-only check' if completeness.get('skipped_enrich') else ''})",
            **{k: completeness.get(k) for k in ("enriched", "passes_gate")},
        )
    except Exception as exc:
        logger.debug("data completeness check skipped: %s", exc)

    macro_trust_multiplier = (
        GATE_FAIL_MACRO_TRUST_MULTIPLIER if not completeness.get("passes_gate") else 1.0
    )

    constituent_mode = "full_refresh" if refresh_constituents else "cached_snapshot"
    if refresh_constituents:
        log.info(
            "constituents",
            "Loading NIFTY 50 constituent research (news, sentiment, calendar, filings)…",
            mode=constituent_mode,
        )

        def _constituent_progress(symbol: str, done: int, total: int) -> None:
            log.info(
                "constituents",
                f"Researched {symbol} ({done}/{total})",
                symbol=symbol,
                progress=done,
                total=total,
            )

        signals = batch_constituent_research(
            lookahead_days=horizon.days,
            refresh=True,
            on_progress=_constituent_progress,
        )
        try:
            from trade_integrations.hub_capture.channel import record_news_headlines

            index_headlines: list[dict[str, Any]] = []
            for signal in signals:
                for factor in signal.factors or []:
                    if factor.get("type") != "news":
                        continue
                    title = str(factor.get("title") or "").strip()
                    if title:
                        index_headlines.append(
                            {
                                "title": title,
                                "summary": factor.get("note"),
                                "source": factor.get("source"),
                            }
                        )
            if index_headlines:
                record_news_headlines("NIFTY", index_headlines[:50], source="index_constituents")
        except Exception:
            pass
    else:
        cached_doc = load_index_research_json(sym)
        if cached_doc is None:
            raise RuntimeError(
                f"No index research snapshot for {sym}; run full analysis with "
                "'Refresh all 50 constituents' checked first"
            )
        signals = signals_from_cached_doc(cached_doc)
        if not signals:
            raise RuntimeError(
                f"Cached index snapshot for {sym} has no constituent signals; "
                "run full analysis with 'Refresh all 50 constituents' checked"
            )
        log.info(
            "constituents",
            f"Using cached constituent snapshot ({len(signals)} stocks) — skipping per-stock research",
            mode=constituent_mode,
            count=len(signals),
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

    try:
        from trade_integrations.hub_capture.ohlcv_cache import prefetch_symbols

        prefetch_symbols_list = [sym] + [s.symbol for s in signals]
        ohlcv_stats = prefetch_symbols(
            prefetch_symbols_list,
            days=14,
            force=refresh_constituents,
        )
        log.info(
            "ohlcv_cache",
            f"OHLCV cache warm — loaded {ohlcv_stats.get('loaded', 0)}/{ohlcv_stats.get('symbols', 0)} "
            f"(cache hits {ohlcv_stats.get('cache_hits', 0)}, vendor fetches {ohlcv_stats.get('vendor_fetches', 0)})",
            **ohlcv_stats,
        )
    except Exception as exc:
        logger.debug("ohlcv prefetch skipped: %s", exc)

    log.info("momentum", "Fetching 7-day price momentum per constituent…")
    momentum_force = True if not refresh_constituents else refresh_constituents
    signals = attach_constituent_momentum(signals, force_refresh=momentum_force)
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
            vendor="batch_constituents" if refresh_constituents else "cached_snapshot",
            fetched_at=now,
            data={
                "count": len(signals),
                "momentum_count": momentum_count,
                "mode": constituent_mode,
            },
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

    global_factors = list(macro_stage.data.get("factor_rows") or [])

    try:
        from trade_integrations.dataflows.index_research.derivatives_bridge import (
            load_derivatives_implied_factors,
        )

        for row in load_derivatives_implied_factors(sym):
            factor = row.get("factor")
            value = row.get("value")
            if factor and value is not None:
                macro_factors[str(factor)] = float(value)
                global_factors.append(row)
    except Exception as exc:
        logger.debug("derivatives bridge skipped: %s", exc)

    momentum_rollup, momentum_source = resolve_constituent_momentum_rollup(
        signals,
        fallback_factors=macro_factors,
    )
    if momentum_rollup is not None:
        macro_factors["constituent_momentum_7d"] = momentum_rollup
        log.info(
            "momentum",
            f"Weighted constituent momentum 7d: {momentum_rollup:.2f}% ({momentum_source})",
            value=momentum_rollup,
            source=momentum_source,
        )
    if momentum_rollup is not None:
        global_factors.append(
            {
                "factor": "constituent_momentum_7d",
                "value": momentum_rollup,
                "source": "constituent_momentum",
            }
        )

    try:
        from trade_integrations.dataflows.index_research.alpha_bridge.snapshot import (
            apply_alpha_zoo_to_macro,
        )

        macro_factors, global_factors = apply_alpha_zoo_to_macro(macro_factors, global_factors)
    except Exception as exc:
        logger.debug("alpha_zoo bridge skipped: %s", exc)

    log.info("spot", f"Fetching live NIFTY spot via OpenAlgo INDstocks / yfinance…")
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

    log.info("scenarios", "Building event scenarios (earnings, RBI, expiry, budget)…")
    scenarios = build_index_scenarios(
        signals,
        macro_factors,
        spot=spot,
        horizon_days=horizon.days,
    ) if spot > 0 else []
    log.info("scenarios", f"{len(scenarios)} scenarios built", events=[s.get("event") for s in scenarios])

    scenario_anchor = (
        scenario_weighted_return_pct(scenarios, spot=spot) if spot > 0 and scenarios else None
    )

    log.info("predict", "Running hybrid predictor (bottom-up + macro Ridge + direction head)…")
    prediction = predict_nifty(
        spot=spot,
        signals=signals,
        macro_factors=macro_factors,
        horizon=horizon,
        as_of_day=now.date().isoformat(),
        scenario_anchor_return_pct=scenario_anchor,
        macro_trust_multiplier=macro_trust_multiplier,
    ) if spot > 0 else {}
    if prediction and not completeness.get("passes_gate"):
        after = completeness.get("after") or {}
        prediction["data_quality_warning"] = {
            "gate": "flow_coverage",
            "min_pct": after.get("min_pct"),
            "threshold_pct": after.get("gate_threshold_pct", 90.0),
            "message": "FII/DII/PCR coverage below gate — macro Ridge down-weighted",
            "macro_trust_multiplier": macro_trust_multiplier,
        }
        prediction["flow_coverage"] = {
            "passes_gate": completeness.get("passes_gate"),
            "min_pct": after.get("min_pct"),
        }
    if prediction:
        prediction["momentum_coverage"] = momentum_coverage_stats(signals)
        try:
            from trade_integrations.knowledge.interpret import build_index_interpretation_bundle

            prediction["interpretation"] = build_index_interpretation_bundle(
                macro_factors,
                horizon_name=horizon.name,
                horizon_days=horizon.days,
                trend_20d=trend,
                prediction=prediction,
                sector_breadth=_sector_breadth(signals),
                ticker=sym,
            )
        except Exception as exc:
            logger.debug("interpretation bundle skipped: %s", exc)
        log.info(
            "predict",
            f"Forecast: {prediction.get('view')} {prediction.get('expected_return_pct'):+.2f}% "
            f"(bottom-up {prediction.get('bottom_up_return_pct'):+.2f}%, "
            f"macro Δ {prediction.get('macro_delta_pct'):+.2f}%)",
            view=prediction.get("view"),
            direction_view=prediction.get("direction_view"),
            expected_return_pct=prediction.get("expected_return_pct"),
        )

    pre_reconcile_snapshot = (
        None
    )
    if spot > 0 and prediction:
        from trade_integrations.dataflows.index_research.prediction_algorithms.pipeline_lab import (
            snapshot_pre_reconcile_prediction,
        )

        pre_reconcile_snapshot = snapshot_pre_reconcile_prediction(prediction)

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
        prediction = finalize_index_prediction(
            prediction,
            spot=spot,
            mae_pct=mae_pct,
            macro_factors=macro_factors,
            scenario_anchor_return_pct=scenario_anchor,
        )

    legacy_prediction = None
    if spot > 0 and prediction:
        from trade_integrations.dataflows.index_research.prediction_algorithms.pipeline_lab import (
            snapshot_legacy_prediction,
        )

        legacy_prediction = snapshot_legacy_prediction(prediction)

    from trade_integrations.context.hub import load_agent_debate_json
    from trade_integrations.research.debate_synthesis import (
        extract_structured_debate,
        merge_index_prediction,
    )

    debate_raw = load_agent_debate_json(sym)
    debate_struct = extract_structured_debate(debate_raw)
    if debate_struct and prediction:
        prediction = merge_index_prediction(debate_struct, prediction)
        artifact = load_stored_model_artifact()
        mae_pct = float(artifact.mae if artifact else 1.5)
        prediction = finalize_index_prediction(
            prediction,
            spot=spot,
            mae_pct=mae_pct,
            macro_factors=macro_factors,
            scenario_anchor_return_pct=scenario_anchor,
        )
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

    if spot > 0 and prediction:
        from trade_integrations.dataflows.index_research.prediction_algorithms.pipeline_lab import (
            attach_forecast_lab,
        )

        prediction = attach_forecast_lab(
            prediction,
            ticker=sym,
            spot=spot,
            horizon_days=horizon.days,
            macro_factors=macro_factors,
            signals=signals,
            scenarios=scenarios,
            scenario_anchor=scenario_anchor,
            as_of_day=now.date().isoformat(),
            macro_trust_multiplier=macro_trust_multiplier,
            debate_payload=debate_raw if debate_struct else None,
            pre_reconcile_snapshot=pre_reconcile_snapshot,
            legacy_prediction=legacy_prediction,
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
        from trade_integrations.dataflows import news_hub_bridge

        macro_map = {
            str(r.get("factor")): float(r.get("value"))
            for r in global_factors
            if r.get("factor") is not None and r.get("value") is not None
        }
        if refresh_constituents:
            news_impact = news_hub_bridge.refresh_news_impact(
                ticker=sym,
                horizon_days=horizon.days,
                spot=float(spot or 0),
                macro_factors=macro_map,
                refresh_ingest=True,
            )
        else:
            news_impact = news_hub_bridge.resolve_news_impact(
                ticker=sym,
                limit=12,
                hydrate_from_hub=True,
            )
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
        event_overlay=(prediction or {}).get("event_overlay") or {},
        news_shock_calibration=_news_shock_summary(),
        stages=stages,
        pipeline_log=log.to_dicts(),
    )
