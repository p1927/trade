"""Pre-specified regime gates for macro equation blocks."""

from __future__ import annotations

from typing import Any

import numpy as np

from trade_integrations.dataflows.index_research.regime import classify_regime

MOMENTUM_FACTORS = frozenset(
    {
        "nifty_return_7d",
        "nifty_return_14d",
        "nifty_rsi_14",
        "nifty_ma20_distance_pct",
        "nifty_ma50_distance_pct",
        "nifty_ma200_distance_pct",
        "nifty_macd_histogram",
        "nifty_bb_percent_b",
        "nifty_adx_14",
        "nifty_atr_pct",
        "nifty_golden_cross_signal",
        "constituent_momentum_7d",
    }
)
FII_CONTRARIAN_FACTORS = frozenset({"fii_net_5d"})
JOINT_FLOW_FACTORS = frozenset({"institutional_net_5d", "dii_absorption_ratio"})
NEWS_EVENT_FACTORS = frozenset(
    {
        "news_material_7d",
        "news_war_7d",
        "news_oil_7d",
        "news_fii_7d",
        "news_rbi_7d",
        "news_crash_theme_7d",
        "news_rally_theme_7d",
        "news_net_tone_7d",
        "news_surprise_7d",
    }
)

_HIGH_FEAR_VIX = 18.0
_TREND_DOWN_PCT = -3.0


def resolve_regime_label(factors: dict[str, Any]) -> str:
    """Return high_fear, trend_down, or range_bound."""
    vix = factors.get("india_vix")
    trend = factors.get("nifty_return_14d") or factors.get("trend_20d_pct")
    try:
        vix_f = float(vix) if vix is not None else None
    except (TypeError, ValueError):
        vix_f = None
    try:
        trend_f = float(trend) if trend is not None else None
    except (TypeError, ValueError):
        trend_f = None

    if vix_f is not None and vix_f > _HIGH_FEAR_VIX:
        return "high_fear"
    if trend_f is not None and trend_f < _TREND_DOWN_PCT:
        return "trend_down"
    regime = classify_regime(
        india_vix=vix_f,
        nifty_trend_20d="down" if trend_f is not None and trend_f < 0 else "sideways",
    )
    if regime.get("label") == "bear" and trend_f is not None and trend_f < _TREND_DOWN_PCT:
        return "trend_down"
    return "range_bound"


def block_gate_weights(regime_label: str) -> dict[str, float]:
    """Pre-specified multipliers — not tuned on miss dates."""
    if regime_label == "high_fear":
        return {
            "momentum": 0.5,
            "flows": 1.0,
            "global": 1.0,
            "vol": 1.0,
            "calendar": 1.0,
            "news_events": 0.5,
        }
    if regime_label == "trend_down":
        return {"momentum": 1.0, "flows": 0.0, "global": 1.0, "vol": 1.0, "calendar": 1.0, "news_events": 1.0}
    return {"momentum": 1.0, "flows": 1.0, "global": 1.0, "vol": 1.0, "calendar": 1.0, "news_events": 1.0}


def factor_gate_weight(factor_name: str, regime_label: str) -> float:
    weights = block_gate_weights(regime_label)
    if factor_name in MOMENTUM_FACTORS:
        return weights["momentum"]
    if factor_name in FII_CONTRARIAN_FACTORS:
        return weights["flows"]
    if factor_name in JOINT_FLOW_FACTORS:
        return weights["flows"]
    if factor_name.startswith("oil_") or factor_name in {"usd_inr", "gold", "sp500", "us_10y"}:
        return weights["global"]
    if factor_name in {"india_vix", "nifty_realized_vol_20d", "india_vix_change_5d"}:
        return weights["vol"]
    if factor_name in {"days_to_monthly_expiry", "is_budget_week", "is_results_season"}:
        return weights["calendar"]
    if factor_name in NEWS_EVENT_FACTORS:
        return weights["news_events"]
    return 1.0


def predict_macro_delta_gated(
    macro_factors: dict[str, Any],
    horizon: Any,
    artifact: Any,
    *,
    macro_trust_multiplier: float = 1.0,
) -> float:
    """Apply pre-specified regime gates to macro Ridge output (no new coefficients)."""
    from trade_integrations.dataflows.index_research.predictor import (
        ModelArtifact,
        _expand_poly,
        _macro_trust_weight,
        _scale_features,
    )

    if artifact is None or not getattr(artifact, "feature_names", None):
        return 0.0

    values: list[float] = []
    gates: list[float] = []
    regime = resolve_regime_label(macro_factors)
    for name in artifact.feature_names:
        raw = macro_factors.get(name, 0.0)
        try:
            val = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            val = 0.0
        values.append(val)
        gates.append(factor_gate_weight(name, regime))

    raw_vec = np.array(values, dtype=float).reshape(1, -1)
    gate_vec = np.array(gates, dtype=float).reshape(1, -1)
    if artifact.feature_means and artifact.feature_stds:
        scaled = _scale_features(raw_vec, artifact.feature_means, artifact.feature_stds)
    else:
        scaled = raw_vec
    gated_input = scaled * gate_vec
    expanded, poly_names = _expand_poly(gated_input, artifact.feature_names, artifact.poly_degree)
    coefs = np.array([artifact.coefficients.get(name, 0.0) for name in poly_names], dtype=float)
    trust = _macro_trust_weight(float(artifact.mae or 1.5)) * max(0.0, macro_trust_multiplier)
    raw_delta = float(artifact.intercept + np.dot(expanded.flatten(), coefs)) * trust
    from trade_integrations.dataflows.index_research.flow_regime_buckets import (
        apply_flow_regime_adjustment,
    )

    return apply_flow_regime_adjustment(raw_delta, macro_factors, regime)


def apply_regime_gates_to_contributions(
    contributors: list[dict[str, Any]],
    *,
    factors: dict[str, Any],
) -> tuple[float, list[dict[str, Any]]]:
    """Scale contributor rows by regime gate; return (gated_sum, gated_rows)."""
    regime = resolve_regime_label(factors)
    gated_rows: list[dict[str, Any]] = []
    total = 0.0
    for row in contributors:
        term = str(row.get("term") or "")
        base_factor = term.split(" ")[0] if term else term
        weight = factor_gate_weight(base_factor, regime)
        contrib = float(row.get("contribution_pct") or 0.0) * weight
        gated_rows.append({**row, "regime_weight": weight, "contribution_pct": round(contrib, 4)})
        total += contrib
    return round(total, 4), gated_rows
