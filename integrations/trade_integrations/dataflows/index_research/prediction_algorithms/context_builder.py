"""Build TrackContext from hub cache or aggregator inputs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trade_integrations.context.hub import load_index_research_json
from trade_integrations.dataflows.index_research.constituent_snapshot import signals_from_cached_doc
from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.models import ConstituentSignal
from trade_integrations.dataflows.index_research.predictor import load_stored_model_artifact
from trade_integrations.dataflows.index_research.prediction_algorithms.types import TrackContext
from trade_integrations.dataflows.index_research.scenarios import scenario_weighted_return_pct


def _macro_from_doc(doc) -> dict[str, Any]:
    factors: dict[str, Any] = {}
    for row in doc.global_factors or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("factor") or "").strip()
        val = row.get("value")
        if key and val is not None:
            try:
                factors[key] = float(val)
            except (TypeError, ValueError):
                continue
    regime = doc.regime or {}
    if regime.get("trend_20d") is not None:
        factors.setdefault("nifty_trend_20d", regime.get("trend_20d"))
    if regime.get("india_vix") is not None:
        factors.setdefault("india_vix", regime.get("india_vix"))
    return factors


def build_track_context(
    *,
    ticker: str,
    spot: float,
    horizon_days: int,
    macro_factors: dict[str, Any] | None = None,
    signals: list[ConstituentSignal] | None = None,
    scenarios: list[dict[str, Any]] | None = None,
    scenario_anchor: float | None = None,
    debate_payload: dict[str, Any] | None = None,
    as_of_day: str | None = None,
    macro_trust_multiplier: float = 1.0,
    prediction_snapshot: dict[str, Any] | None = None,
    legacy_prediction: dict[str, Any] | None = None,
) -> TrackContext:
    horizon = resolve_horizon(horizon_days)
    anchor = scenario_anchor
    if anchor is None and spot > 0 and scenarios:
        anchor = scenario_weighted_return_pct(scenarios, spot=spot)

    factors = dict(macro_factors or {})
    if as_of_day:
        try:
            from trade_integrations.dataflows.index_research.event_overlay import enrich_macro_with_news_features

            factors = enrich_macro_with_news_features(factors, as_of_day=as_of_day, ticker=ticker.strip().upper())
        except Exception:
            pass

    return TrackContext(
        ticker=ticker.strip().upper(),
        spot=float(spot or 0.0),
        horizon=horizon,
        macro_factors=factors,
        signals=list(signals or []),
        scenarios=list(scenarios or []),
        scenario_anchor=anchor,
        debate_payload=debate_payload,
        model_artifact=load_stored_model_artifact(),
        as_of_day=as_of_day or datetime.now(timezone.utc).date().isoformat(),
        macro_trust_multiplier=macro_trust_multiplier,
        prediction_snapshot=prediction_snapshot,
        legacy_prediction=legacy_prediction,
    )


def context_from_hub(
    ticker: str = "NIFTY",
    *,
    horizon_days: int | None = None,
    use_legacy_prediction: bool = True,
) -> TrackContext | None:
    """Build context from cached hub index research artifact."""
    doc = load_index_research_json(ticker.strip().upper())
    if doc is None:
        return None

    days = horizon_days
    if days is None:
        days = int((doc.horizon or {}).get("days") or 14)

    spot = float(doc.spot or 0.0)
    scenarios = list(doc.scenarios or [])
    signals = signals_from_cached_doc(doc)
    macro = _macro_from_doc(doc)
    prediction = dict(doc.prediction or {})
    lab_ctx = dict(prediction.get("forecast_lab_context") or {})
    pre_reconcile = lab_ctx.get("pre_reconcile_snapshot")
    legacy = lab_ctx.get("legacy_prediction")
    if not legacy and use_legacy_prediction:
        legacy = {
            "expected_return_pct": prediction.get("expected_return_pct"),
            "view": prediction.get("view"),
            "reconciled_with_scenarios": prediction.get("reconciled_with_scenarios"),
            "debate_merged": prediction.get("debate_merged"),
        }

    debate_payload = None
    try:
        from trade_integrations.context.hub import load_agent_debate_json

        debate_payload = load_agent_debate_json(ticker.strip().upper())
    except Exception:
        debate_payload = None

    as_of = doc.as_of.date().isoformat() if hasattr(doc.as_of, "date") else None
    return build_track_context(
        ticker=ticker,
        spot=spot,
        horizon_days=days,
        macro_factors=macro,
        signals=signals,
        scenarios=scenarios,
        debate_payload=debate_payload,
        as_of_day=as_of,
        prediction_snapshot=pre_reconcile if isinstance(pre_reconcile, dict) else None,
        legacy_prediction=legacy if use_legacy_prediction and isinstance(legacy, dict) else None,
    )
