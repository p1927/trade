"""Rule-based India quant review — second opinion separate from Ridge."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_INDEX_TICKERS = frozenset(
    {"NIFTY", "NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "^NSEI"}
)


def _merge_factor_maps(*maps: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for m in maps:
        for key, raw in m.items():
            if raw is None:
                continue
            try:
                out[key] = float(raw)
            except (TypeError, ValueError):
                continue
    return out


def _live_openalgo_technical_factors(ticker: str) -> dict[str, float]:
    """Best-effort live TA from Nifty history (same stack as index pipeline)."""
    from trade_integrations.dataflows.index_research.sources.history_loader import (
        load_nifty_history,
    )
    from trade_integrations.dataflows.index_research.technical_features import (
        latest_technical_factor_dict,
    )

    if ticker.strip().upper() not in _INDEX_TICKERS:
        return {}
    hist = load_nifty_history(days=280)
    return latest_technical_factor_dict(hist)


def build_quant_review_payload(
    ticker: str,
    *,
    horizon_days: int | None = None,
) -> dict[str, Any]:
    """Assemble quant review JSON from hub index research + live factors."""
    from trade_integrations.context.hub import load_index_research_json
    from trade_integrations.dataflows.index_research.aggregator import _nifty_trend_20d
    from trade_integrations.dataflows.index_research.horizon import resolve_horizon
    from trade_integrations.dataflows.index_research.derivatives_bridge import (
        load_derivatives_implied_factors,
    )
    from trade_integrations.knowledge.interpret import (
        build_index_interpretation_bundle,
        build_surprises,
        detect_forecast_disagreements,
        resolve_active_strategy_profile,
    )

    key = ticker.strip().upper()
    now = datetime.now(timezone.utc)
    horizon = resolve_horizon(horizon_days)
    index_doc = load_index_research_json(key)
    prediction: dict[str, Any] = {}
    hub_factors: dict[str, float] = {}
    spot = None

    sector_breadth: dict[str, Any] = {}
    if index_doc is not None:
        if hasattr(index_doc, "model_dump"):
            payload = index_doc.model_dump()
        elif isinstance(index_doc, dict):
            payload = index_doc
        else:
            payload = {}
        prediction = payload.get("prediction") or {}
        spot = payload.get("spot")
        sector_breadth = payload.get("sector_breadth") or {}
        for row in payload.get("global_factors") or []:
            factor = row.get("factor")
            value = row.get("value")
            if factor and value is not None:
                try:
                    hub_factors[str(factor)] = float(value)
                except (TypeError, ValueError):
                    pass

    live_ta = _live_openalgo_technical_factors(key)
    deriv_rows = load_derivatives_implied_factors(key)
    deriv_map = {r["factor"]: r["value"] for r in deriv_rows}
    factors = _merge_factor_maps(hub_factors, live_ta, deriv_map)

    trend = _nifty_trend_20d()
    interpretation = build_index_interpretation_bundle(
        factors,
        horizon_name=horizon.name,
        horizon_days=horizon.days,
        trend_20d=trend,
        prediction=prediction,
        sector_breadth=sector_breadth,
        ticker=key,
    )
    disagreements = detect_forecast_disagreements(factors, prediction, trend_20d=trend)
    surprises = build_surprises(factors, prediction, trend_20d=trend)
    profile = resolve_active_strategy_profile(
        factors,
        horizon_name=horizon.name,
        trend_20d=trend,
        sector_breadth=sector_breadth,
    )

    ta_direction = "neutral"
    hist = factors.get("nifty_macd_histogram")
    rsi = factors.get("nifty_rsi_14")
    if hist is not None:
        if hist > 2:
            ta_direction = "bullish"
        elif hist < -2:
            ta_direction = "bearish"
    if rsi is not None and ta_direction == "neutral":
        if rsi > 55:
            ta_direction = "bullish"
        elif rsi < 45:
            ta_direction = "bearish"

    review_confidence = 0.55
    if disagreements:
        review_confidence = 0.65
    if len(surprises) >= 3:
        review_confidence = min(0.85, review_confidence + 0.1)

    return {
        "ticker": key,
        "as_of": now.isoformat(),
        "horizon_days": horizon.days,
        "horizon_name": horizon.name,
        "spot": spot,
        "data_freshness": {
            "hub_index_research": bool(index_doc),
            "live_technical_keys": sorted(live_ta.keys()),
            "derivatives_bridge_keys": sorted(deriv_map.keys()),
        },
        "ta_consensus": {
            "direction": ta_direction,
            "confidence": round(review_confidence, 2),
            "key_levels_note": interpretation.get("technical_interpretation"),
        },
        "active_strategy_profile": profile.get("key"),
        "strategy_profile": profile,
        "strategy_context": interpretation.get("strategy_context"),
        "strategy_options_handoff": interpretation.get("strategy_options_handoff"),
        "risk_notes": interpretation.get("risk_notes"),
        "factor_notes": interpretation.get("factor_notes"),
        "technical_interpretation": interpretation.get("technical_interpretation"),
        "technical_readings": interpretation.get("technical_readings"),
        "surprises": surprises,
        "disagreements_with_forecast": disagreements,
        "review_confidence": round(review_confidence, 2),
        "disclaimer": "Reviewer opinion — separate from Ridge headline forecast.",
        "model_prediction_view": prediction.get("view"),
        "model_expected_return_pct": prediction.get("expected_return_pct"),
    }


def run_quant_review(
    ticker: str,
    *,
    horizon_days: int | None = None,
    save: bool = True,
) -> dict[str, Any]:
    """Build and optionally persist quant review artifact."""
    from trade_integrations.context.hub import save_quant_review

    payload = build_quant_review_payload(ticker, horizon_days=horizon_days)
    if save:
        save_quant_review(ticker, payload)
        logger.info("Saved quant review for %s", ticker)
    return payload
