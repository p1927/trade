"""Lightweight index prediction refresh (macro + cached constituents)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from trade_integrations.context.hub import load_index_research_json, save_index_research
from trade_integrations.dataflows.index_research.constituent_snapshot import signals_from_cached_doc
from trade_integrations.dataflows.company_research.models import StageResult
from trade_integrations.dataflows.index_research.attribution import (
    attribute_constituents,
    rollup_attribution,
)
from trade_integrations.dataflows.index_research.constituent_momentum import (
    momentum_coverage_stats,
    resolve_constituent_momentum_rollup,
)
from trade_integrations.dataflows.index_research.explain import build_factor_explanation_bundle
from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.macro_global import fetch_global_macro_snapshot
from trade_integrations.dataflows.index_research.factor_store import upsert_daily_factors
from trade_integrations.dataflows.index_research.models import ConstituentSignal, IndexResearchDoc, PredictionRecord
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger
from trade_integrations.dataflows.index_research.prediction_ledger import (
    append_prediction,
    build_prediction_metadata,
)
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

logger = logging.getLogger(__name__)

_MACRO_DRIFT_ENV = "INDEX_MONITOR_MACRO_DRIFT_PCT"
_DEFAULT_MACRO_DRIFT_PCT = 0.5
_LIGHT_REFRESH_HEAVY_ENV = "INDEX_LIGHT_REFRESH_HEAVY"
_LIGHT_REFRESH_BUDGET_ENV = "INDEX_LIGHT_REFRESH_BUDGET_SEC"
_DEFAULT_LIGHT_REFRESH_BUDGET_SEC = 420.0


def _light_refresh_heavy_enabled(*, poll_mode: bool) -> bool:
    if poll_mode:
        return os.environ.get(_LIGHT_REFRESH_HEAVY_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}
    return True


def _light_refresh_budget_sec() -> float:
    try:
        return max(60.0, float(os.getenv(_LIGHT_REFRESH_BUDGET_ENV, str(_DEFAULT_LIGHT_REFRESH_BUDGET_SEC))))
    except ValueError:
        return _DEFAULT_LIGHT_REFRESH_BUDGET_SEC


class _LightRefreshBudgetExceeded(TimeoutError):
    pass


def _check_light_refresh_budget(deadline: float, stage: str) -> None:
    import time as _time

    if _time.monotonic() > deadline:
        raise _LightRefreshBudgetExceeded(f"light_refresh budget exceeded at {stage}")


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _overlay_summary() -> dict[str, Any]:
    try:
        from trade_integrations.dataflows.index_research.event_overlay import overlay_summary_for_ui

        return overlay_summary_for_ui("NIFTY")
    except Exception:
        return {}


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


def _headline_titles(headlines: list[Any]) -> list[str]:
    """Extract title strings from MaterialHeadline objects or plain strings."""
    titles: list[str] = []
    for item in headlines:
        if isinstance(item, str):
            titles.append(item)
            continue
        title = getattr(item, "title", None)
        if title:
            titles.append(str(title))
    return titles


def _material_news_for_index(ticker: str) -> list[Any]:
    try:
        from trade_integrations.monitor.news_watcher import check_material_news
    except ImportError:
        return []
    since = _news_since_for_index(ticker)
    try:
        return check_material_news(ticker, since)
    except Exception as exc:
        logger.warning("material news check failed for %s: %s", ticker, exc)
        return []


def _heavyweight_news(
    signals: list[ConstituentSignal],
    *,
    headlines: list[Any] | None = None,
) -> bool:
    try:
        if headlines is None:
            headlines = _material_news_for_index("NIFTY")
        if not headlines:
            return False
        top_symbols = {signal.symbol for signal in signals[:10]}
        joined = " ".join(_headline_titles(headlines)).upper()
        return any(sym in joined for sym in top_symbols)
    except Exception as exc:
        logger.warning("heavyweight news check failed: %s", exc)
        return False


def _coerce_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _concurrent_full_run_saved(
    *,
    disk_as_of_at_start: datetime | None,
    existing: IndexResearchDoc | None,
) -> bool:
    """True when a full analysis saved to hub while this light refresh was running."""
    if existing is None or disk_as_of_at_start is None:
        return False
    existing_as_of = _coerce_utc(getattr(existing, "as_of", None))
    if existing_as_of is None or existing_as_of <= disk_as_of_at_start:
        return False
    pipeline_log = list(getattr(existing, "pipeline_log", None) or [])
    return any(row.get("stage") == "done" for row in pipeline_log if isinstance(row, dict))


def _reload_index_doc(
    ticker: str,
    fallback: IndexResearchDoc | None,
) -> IndexResearchDoc | None:
    """Reload hub artifact, falling back when disk read is transiently unavailable."""
    try:
        fresh = load_index_research_json(ticker)
    except Exception as exc:
        logger.debug("light_refresh hub reload skipped: %s", exc)
        return fallback
    return fresh if fresh is not None else fallback


def _strip_spot_data_warnings(warnings: list[str] | None) -> list[str]:
    """Drop stale live-spot warnings once a fresh spot fetch succeeds."""
    if not warnings:
        return []
    kept: list[str] = []
    for warning in warnings:
        text = str(warning)
        lowered = text.lower()
        if text.startswith("Live spot unavailable:"):
            continue
        if "vendor_zero_ltp" in lowered:
            continue
        if "openalgo_quotes_circuit_open" in lowered:
            continue
        kept.append(text)
    return kept


def _try_spot_touch(
    sym: str,
    cached_doc: IndexResearchDoc,
    *,
    horizon,
) -> tuple[IndexResearchDoc | None, str | None]:
    """Fetch live spot and persist when macro/news are unchanged."""
    from dataclasses import replace

    from trade_integrations.context.hub import save_index_research
    from trade_integrations.dataflows.index_research.spot_fetch import fetch_index_spot

    spot_result = fetch_index_spot(sym)
    if spot_result.spot <= 0:
        return None, None

    refresh_at = _stage_now()
    doc = replace(
        cached_doc,
        spot=spot_result.spot,
        spot_source=spot_result.source,
        spot_error=None,
        data_warnings=_strip_spot_data_warnings(cached_doc.data_warnings),
        as_of=refresh_at,
    )
    save_index_research(doc)
    logger.info("light_refresh spot_touch for %s spot=%.2f", sym, spot_result.spot)
    return doc, "spot_touch"


def run_index_light_refresh(
    ticker: str = "NIFTY",
    *,
    horizon_days: int | None = None,
    force: bool = False,
    poll_mode: bool = False,
) -> tuple[IndexResearchDoc, str]:
    """Recompute prediction using cached constituents and fresh macro factors."""
    import time as _time

    sym = ticker.strip().upper()
    horizon = resolve_horizon(horizon_days)
    deadline = _time.monotonic() + _light_refresh_budget_sec()
    heavy = _light_refresh_heavy_enabled(poll_mode=poll_mode)
    cached_doc = load_index_research_json(sym)
    disk_as_of_at_start = _coerce_utc(getattr(cached_doc, "as_of", None) if cached_doc else None)

    if cached_doc is None and not force:
        raise RuntimeError(
            f"No index research snapshot for {sym}; run full analysis before enabling live refresh"
        )

    signals = signals_from_cached_doc(cached_doc)
    momentum_count = sum(1 for s in signals if s.momentum_7d_pct is not None)

    previous_factors: dict[str, Any] = {}
    if cached_doc and cached_doc.global_factors:
        for row in cached_doc.global_factors:
            if isinstance(row, dict) and row.get("factor") is not None:
                previous_factors[str(row["factor"])] = row.get("value")

    sentiments = [s.sentiment_score for s in signals if s.sentiment_score is not None]
    _check_light_refresh_budget(deadline, "before_macro")
    macro_stage = fetch_global_macro_snapshot(
        constituent_sentiments=sentiments or None,
        trading_day=_stage_now().date().isoformat(),
        force=False,
    )
    macro_factors = dict(macro_stage.data.get("factors") or {})
    global_factors = list(macro_stage.data.get("factor_rows") or [])

    macro_changed = _macro_factor_changed(
        previous_factors,
        macro_factors,
        threshold_pct=_macro_drift_threshold(),
    )

    # Poll ticks run every 5 minutes: skip live news-aggregator I/O when macro is
    # stable (hub news ingest handles material headlines separately).
    if poll_mode and not force and cached_doc is not None and not macro_changed:
        _check_light_refresh_budget(deadline, "before_spot_touch")
        touched, reason = _try_spot_touch(sym, cached_doc, horizon=horizon)
        if touched is not None and reason:
            return touched, reason
        fresh = _reload_index_doc(sym, cached_doc)
        return fresh or cached_doc, "unchanged"

    headlines: list[Any] = []
    if poll_mode:
        news_hit = False
    else:
        headlines = _material_news_for_index(sym)
        news_hit = bool(headlines) or _heavyweight_news(signals, headlines=headlines)

    if not force and cached_doc is not None and not macro_changed and not news_hit:
        touched, reason = _try_spot_touch(sym, cached_doc, horizon=horizon)
        if touched is not None and reason:
            return touched, reason
        fresh = _reload_index_doc(sym, cached_doc)
        return fresh or cached_doc, "unchanged"

    reason = "material_news" if news_hit else "macro_drift" if macro_changed else "forced"

    try:
        from trade_integrations.dataflows.index_research.constituent_momentum import attach_constituent_momentum
        from trade_integrations.hub_capture.ohlcv_cache import prefetch_symbols

        _check_light_refresh_budget(deadline, "before_momentum")
        prefetch_list = [sym] + [s.symbol for s in signals]
        ohlcv_stats = prefetch_symbols(prefetch_list, days=14, force=False)
        signals = attach_constituent_momentum(signals, force_refresh=not poll_mode)
        momentum_count = sum(1 for s in signals if s.momentum_7d_pct is not None)
        logger.info(
            "light_refresh ohlcv_cache loaded=%s cache_hits=%s vendor_fetches=%s momentum=%s",
            ohlcv_stats.get("loaded"),
            ohlcv_stats.get("cache_hits"),
            ohlcv_stats.get("vendor_fetches"),
            momentum_count,
        )
    except Exception as exc:
        logger.debug("light_refresh momentum refresh skipped: %s", exc)

    if heavy:
        try:
            from trade_integrations.dataflows.index_research.history_ingest import sync_nifty_ohlcv_tail

            sync_nifty_ohlcv_tail()
        except Exception as exc:
            logger.debug("light_refresh nifty ohlcv tail skipped: %s", exc)
        try:
            from datetime import date as _date

            from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
                merge_flow_derivatives_frame,
                upsert_flow_cash_cache,
            )

            _check_light_refresh_budget(deadline, "before_flow_sync")
            try:
                from trade_integrations.dataflows.index_research.history_ingest import run_history_incremental_sync

                run_history_incremental_sync(days=30, explicit=False)
            except Exception as ingest_exc:
                logger.debug("history incremental sync skipped: %s", ingest_exc)
                try:
                    from trade_integrations.nse_browser.repository import ingest_repository_to_hub

                    ingest_repository_to_hub(skip_repo_sync=True, allow_live_fetch=False, explicit=False)
                except Exception as hub_exc:
                    logger.debug("nse repo ingest skipped: %s", hub_exc)

            today = _date.today().isoformat()
            if os.environ.get("NSE_BROWSER_ON_REFRESH", "").strip().lower() in {"1", "true", "yes"}:
                try:
                    from trade_integrations.dataflows.index_research.nse_browser_refresh import (
                        refresh_nse_browser_for_prediction,
                    )

                    refresh_nse_browser_for_prediction(days=30, refresh=False)
                except Exception as browser_exc:
                    logger.debug("nse_browser light_refresh skipped: %s", browser_exc)
            flow = merge_flow_derivatives_frame(today, today, allow_live_fetch=False)
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
    else:
        logger.debug("light_refresh heavy flow/history sync skipped (poll_mode)")

    try:
        from datetime import date as _date

        from trade_integrations.dataflows.index_research.alpha_bridge.snapshot import (
            apply_alpha_zoo_to_macro,
        )

        macro_factors, global_factors = apply_alpha_zoo_to_macro(
            macro_factors,
            global_factors,
            as_of_day=_date.today().isoformat(),
        )
    except Exception as exc:
        logger.debug("alpha_zoo light_refresh skipped: %s", exc)

    momentum_rollup, momentum_source = resolve_constituent_momentum_rollup(
        signals,
        fallback_factors=macro_factors,
    )
    if momentum_rollup is not None:
        macro_factors["constituent_momentum_7d"] = momentum_rollup
        global_factors.append(
            {
                "factor": "constituent_momentum_7d",
                "value": momentum_rollup,
                "source": momentum_source,
            }
        )

    log = PipelineLogger()
    log.info(
        "light_refresh",
        f"Light refresh — horizon {horizon.days}d (profile {horizon.name}), trigger: {reason}",
        ticker=sym,
        reason=reason,
    )
    log.info(
        "constituents",
        f"Using {len(signals)} cached constituent signals (momentum on {momentum_count})",
        count=len(signals),
        momentum_count=momentum_count,
    )
    log.info(
        "macro",
        f"Macro snapshot: {len(macro_factors)} factors ({macro_stage.status})",
        status=macro_stage.status,
    )

    from trade_integrations.dataflows.index_research.spot_fetch import fetch_index_spot

    spot_result = fetch_index_spot(sym)
    spot = spot_result.spot
    spot_source = spot_result.source
    spot_error = spot_result.error
    data_warnings: list[str] = list(getattr(cached_doc, "data_warnings", None) or [])
    if spot_error:
        data_warnings.append(f"Live spot unavailable: {spot_error}")
    log.info(
        "spot",
        f"Spot: {spot:.2f} ({spot_source})" if spot > 0 else f"Spot unavailable ({spot_error or 'openalgo'})",
        spot=spot,
        spot_source=spot_source,
    )

    from trade_integrations.dataflows.index_research.data_completeness import (
        GATE_FAIL_MACRO_TRUST_MULTIPLIER,
        measure_flow_coverage,
    )

    flow_coverage = measure_flow_coverage(allow_live_fetch=False)
    macro_trust_multiplier = (
        GATE_FAIL_MACRO_TRUST_MULTIPLIER if not flow_coverage.get("passes_gate") else 1.0
    )

    regime = classify_regime(
        india_vix=macro_factors.get("india_vix"),
        nifty_trend_20d=_nifty_trend_20d(),
    )

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
    scenario_anchor = (
        scenario_weighted_return_pct(scenarios, spot=spot) if spot > 0 and scenarios else None
    )

    prediction = (
        predict_nifty(
            spot=spot,
            signals=signals,
            macro_factors=macro_factors,
            horizon=horizon,
            as_of_day=_stage_now().date().isoformat(),
            scenario_anchor_return_pct=scenario_anchor,
            macro_trust_multiplier=macro_trust_multiplier,
        )
        if spot > 0
        else {}
    )
    if prediction and not flow_coverage.get("passes_gate"):
        prediction["data_quality_warning"] = {
            "gate": "flow_coverage",
            "min_pct": flow_coverage.get("min_pct"),
            "threshold_pct": flow_coverage.get("gate_threshold_pct", 90.0),
            "message": "FII/DII/PCR coverage below gate — macro Ridge down-weighted",
            "macro_trust_multiplier": macro_trust_multiplier,
        }
    if prediction:
        prediction["flow_coverage"] = {
            "passes_gate": flow_coverage.get("passes_gate"),
            "min_pct": flow_coverage.get("min_pct"),
        }
        ret = prediction.get("expected_return_pct")
        ret_text = f"{ret:+.2f}%" if isinstance(ret, (int, float)) else "n/a"
        log.info(
            "predict",
            f"Forecast: {prediction.get('view')} {ret_text}",
            view=prediction.get("view"),
            expected_return_pct=ret,
        )
    as_of_day = _stage_now().date().isoformat()
    attributed = attribute_constituents(
        signals,
        horizon_days=horizon.days,
        as_of_day=as_of_day,
    )
    rollup = rollup_attribution(attributed)
    if prediction:
        prediction["top_drivers"] = rollup.get("top_drivers", [])[:5]
        prediction["momentum_coverage"] = momentum_coverage_stats(signals)
        try:
            from trade_integrations.knowledge.interpret import build_index_interpretation_bundle

            prediction["interpretation"] = build_index_interpretation_bundle(
                macro_factors,
                horizon_name=horizon.name,
                horizon_days=horizon.days,
                trend_20d=_nifty_trend_20d(),
                prediction=prediction,
                sector_breadth=_sector_breadth(signals),
                ticker=sym,
            )
        except Exception as exc:
            logger.debug("interpretation bundle skipped: %s", exc)

    if spot > 0 and prediction and scenarios:
        artifact = load_stored_model_artifact()
        mae_pct = float(artifact.mae if artifact else 1.5)
        prediction = reconcile_prediction_with_scenarios(
            prediction,
            scenarios,
            spot=spot,
            mae_pct=mae_pct,
        )
        prediction = finalize_index_prediction(
            prediction,
            spot=spot,
            mae_pct=mae_pct,
            macro_factors=macro_factors,
            scenario_anchor_return_pct=scenario_anchor,
        )

    if spot > 0 and prediction:
        from trade_integrations.dataflows.index_research.prediction_algorithms.pipeline_lab import (
            attach_forecast_lab,
            snapshot_legacy_prediction,
            snapshot_pre_reconcile_prediction,
        )

        pre_reconcile = snapshot_pre_reconcile_prediction(prediction)
        legacy = snapshot_legacy_prediction(prediction)
        prediction = attach_forecast_lab(
            prediction,
            ticker=sym,
            spot=spot,
            horizon_days=horizon.days,
            macro_factors=macro_factors,
            signals=signals,
            scenarios=scenarios,
            scenario_anchor=scenario_anchor,
            as_of_day=_stage_now().date().isoformat(),
            macro_trust_multiplier=macro_trust_multiplier,
            pre_reconcile_snapshot=pre_reconcile,
            legacy_prediction=legacy,
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
            vendor="index_snapshot_cached",
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

    news_impact: dict[str, Any] = getattr(cached_doc, "news_impact", None) or {}
    try:
        from trade_integrations.dataflows import news_hub_bridge

        if news_hit:
            macro_map = {
                str(r.get("factor")): float(r.get("value"))
                for r in global_factors
                if r.get("factor") is not None and r.get("value") is not None
            }
            news_impact = news_hub_bridge.refresh_news_impact(
                ticker=sym,
                horizon_days=horizon.days,
                spot=float(spot or 0),
                macro_factors=macro_map,
                refresh_ingest=True,
            )
        else:
            news_impact = news_hub_bridge.resolve_news_impact(ticker=sym, doc=cached_doc, limit=12)
    except Exception as exc:
        logger.debug("light_refresh news_impact skipped: %s", exc)

    refresh_at = _stage_now()
    existing_on_disk = _reload_index_doc(sym, cached_doc)
    if _concurrent_full_run_saved(
        disk_as_of_at_start=disk_as_of_at_start,
        existing=existing_on_disk,
    ):
        logger.info(
            "light_refresh skipped save for %s — full analysis saved during refresh",
            sym,
        )
        return existing_on_disk, "superseded_by_full_run"

    log.info("done", "Light refresh complete — saving hub artifact", reason=reason)

    doc = IndexResearchDoc(
        ticker=sym,
        as_of=refresh_at,
        horizon={"name": horizon.name, "days": horizon.days},
        spot=spot or None,
        spot_source=spot_source if spot > 0 else "unavailable",
        spot_error=spot_error,
        data_warnings=data_warnings,
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
        upcoming_events=(cached_doc.upcoming_events if cached_doc else []),
        cascade_calibration=(getattr(cached_doc, "cascade_calibration", None) or {}),
        news_impact=news_impact,
        event_overlay=(prediction or {}).get("event_overlay") or {},
        news_shock_calibration=_overlay_summary(),
        pipeline_log=log.to_dicts(),
        stages=stages,
    )
    save_index_research(doc)
    return doc, reason
