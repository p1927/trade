"""Cause stress index (H1) — news + vol regime composite."""

from __future__ import annotations

from typing import Any

_TOPIC_KEYS = ("news_war_7d", "news_oil_7d", "news_fii_7d", "news_rbi_7d")


def _finite(raw: Any, default: float = 0.0) -> float:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    if val != val:
        return default
    return val


def _norm_feature(value: float, *, scale: float = 5.0) -> float:
    return min(1.0, max(0.0, value / scale))


def _regime_multiplier(macro_factors: dict[str, Any]) -> float:
    vix = _finite(macro_factors.get("india_vix"), 14.0)
    trend = str(macro_factors.get("nifty_trend_20d") or macro_factors.get("trend_20d") or "").lower()
    if vix >= 20:
        return 1.0
    if trend in {"down", "bearish"}:
        return 0.7
    return 0.5


def _active_topic_weight(macro_factors: dict[str, Any]) -> float:
    active = 0.0
    for key in _TOPIC_KEYS:
        if _finite(macro_factors.get(key)) >= 1.0:
            active += 0.25
    return min(1.0, active)


def _active_causes(macro_factors: dict[str, Any]) -> list[str]:
    mapping = {
        "news_war_7d": "geopolitical_war",
        "news_oil_7d": "oil_supply",
        "news_fii_7d": "fii_flow_shock",
        "news_rbi_7d": "rbi_policy",
    }
    out: list[str] = []
    for key, cause_id in mapping.items():
        if _finite(macro_factors.get(key)) >= 1.0:
            out.append(cause_id)
    if _finite(macro_factors.get("is_budget_week")) >= 0.5:
        out.append("budget")
    if _finite(macro_factors.get("is_results_season")) >= 0.5:
        out.append("earnings_cluster")
    return out


def compute_cause_stress_index(macro_factors: dict[str, Any]) -> dict[str, Any]:
    material = _norm_feature(_finite(macro_factors.get("news_material_7d")))
    surprise = _norm_feature(_finite(macro_factors.get("news_surprise_7d")))
    topic = _active_topic_weight(macro_factors)
    vix = _norm_feature(_finite(macro_factors.get("india_vix"), 14.0), scale=25.0)
    regime = _regime_multiplier(macro_factors)

    stress = 25 * material + 25 * surprise + 20 * topic + 15 * vix + 15 * regime
    stress = max(0.0, min(100.0, stress))

    if stress >= 60:
        label = "event_driven"
    elif stress >= 30:
        label = "elevated"
    else:
        label = "calm"

    material_raw = _finite(macro_factors.get("news_material_7d"))
    surprise_raw = _finite(macro_factors.get("news_surprise_7d"))
    unmodeled_event_suspected = stress >= 60 and material_raw < 1.0 and surprise_raw < 1.0

    return {
        "cause_stress_index": round(stress, 2),
        "cause_stress_label": label,
        "active_causes": _active_causes(macro_factors),
        "recommended_refresh": stress >= 60,
        "unmodeled_event_suspected": unmodeled_event_suspected,
    }
