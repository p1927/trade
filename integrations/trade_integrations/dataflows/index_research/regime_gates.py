"""Pre-specified regime gates for macro equation blocks."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.index_research.regime import classify_regime

MOMENTUM_FACTORS = frozenset(
    {
        "nifty_return_7d",
        "nifty_return_14d",
        "nifty_rsi_14",
        "nifty_ma20_distance_pct",
        "constituent_momentum_7d",
    }
)
FII_CONTRARIAN_FACTORS = frozenset({"fii_net_5d", "fii_net_5d_change_5d"})

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
        return {"momentum": 0.5, "flows": 1.0, "global": 1.0, "vol": 1.0, "calendar": 1.0, "delta": 1.0}
    if regime_label == "trend_down":
        return {"momentum": 1.0, "flows": 0.0, "global": 1.0, "vol": 1.0, "calendar": 1.0, "delta": 1.0}
    return {"momentum": 1.0, "flows": 1.0, "global": 1.0, "vol": 1.0, "calendar": 1.0, "delta": 1.0}


def factor_gate_weight(factor_name: str, regime_label: str) -> float:
    weights = block_gate_weights(regime_label)
    if factor_name in MOMENTUM_FACTORS:
        return weights["momentum"]
    if factor_name in FII_CONTRARIAN_FACTORS:
        return weights["flows"]
    if factor_name.startswith("oil_") or factor_name in {"usd_inr", "gold", "sp500", "us_10y"}:
        return weights["global"]
    if factor_name in {"india_vix", "nifty_realized_vol_20d", "india_vix_change_5d"}:
        return weights["vol"]
    if factor_name in {"days_to_monthly_expiry", "is_budget_week", "is_results_season"}:
        return weights["calendar"]
    if factor_name.endswith("_change_5d") or factor_name.endswith("_change_7d"):
        return weights["delta"]
    return 1.0


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
