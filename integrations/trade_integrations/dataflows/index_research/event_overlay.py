"""Event shock overlay — Layer 2 news calibration applied at inference."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trade_integrations.dataflows.index_research.news_event_features import (
    NEWS_EVENT_FACTOR_KEYS,
    is_news_overlay_enabled,
    load_news_model_config,
)
from trade_integrations.dataflows.index_research.news_shock_calibration import load_shock_calibration

_OVERLAY_CAP_PCT = 2.0
_MATERIAL_CLUSTER_THRESHOLD = 3.0

_TOPIC_TO_FEATURE: dict[str, str] = {
    "war": "news_war_7d",
    "oil": "news_oil_7d",
    "fii": "news_fii_7d",
    "rbi": "news_rbi_7d",
    "us_markets": "news_fii_7d",
}


def _finite(raw: Any, default: float = 0.0) -> float:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    if val != val:  # NaN
        return default
    return val


def _topic_active(
    topic: str,
    macro_factors: dict[str, Any],
    *,
    material_count: float,
    surprise_count: float,
) -> bool:
    feature = _TOPIC_TO_FEATURE.get(topic)
    if not feature:
        return False
    intensity = _finite(macro_factors.get(feature))
    if intensity < 1.0:
        return False
    if material_count >= _MATERIAL_CLUSTER_THRESHOLD:
        return True
    return surprise_count >= 1.0


def compute_event_overlay(
    macro_factors: dict[str, Any],
    *,
    as_of_day: str | None = None,
    shock_table: dict[str, Any] | None = None,
    horizon_days: int = 14,
    ticker: str = "NIFTY",
) -> dict[str, Any]:
    """Apply empirical news shock overlay from calibrated topic errors."""
    del as_of_day, horizon_days  # reserved for future path-dependent overlays

    if not is_news_overlay_enabled(ticker):
        return {
            "return_pct": 0.0,
            "active_topics": [],
            "method": "disabled",
            "calibration_as_of": None,
        }

    table = shock_table or load_shock_calibration(ticker) or {}
    topics_table = table.get("topics") or {}
    if not topics_table:
        return {
            "return_pct": 0.0,
            "active_topics": [],
            "method": "no_calibration",
            "calibration_as_of": table.get("as_of"),
        }

    material = _finite(macro_factors.get("news_material_7d"))
    surprise = _finite(macro_factors.get("news_surprise_7d"))
    overlay = 0.0
    active: list[dict[str, Any]] = []

    for topic, entry in topics_table.items():
        if not entry.get("overlay_eligible"):
            continue
        if not _topic_active(topic, macro_factors, material_count=material, surprise_count=surprise):
            continue
        shrink = _finite(entry.get("shrink_weight"), 0.0)
        error = _finite(entry.get("median_calibration_error"))
        contribution = shrink * error
        overlay += contribution
        active.append(
            {
                "topic": topic,
                "contribution_pct": round(contribution, 4),
                "sample_count": entry.get("sample_count"),
            }
        )

    capped = max(-_OVERLAY_CAP_PCT, min(_OVERLAY_CAP_PCT, overlay))
    return {
        "return_pct": round(capped, 4),
        "raw_return_pct": round(overlay, 4),
        "active_topics": active,
        "method": "calibrated_ledger_v1",
        "calibration_as_of": table.get("as_of"),
        "capped": abs(overlay) > _OVERLAY_CAP_PCT,
    }


def merge_overlay_into_macro(
    macro_delta_pct: float,
    macro_factors: dict[str, Any],
    *,
    as_of_day: str | None = None,
    ticker: str = "NIFTY",
) -> tuple[float, dict[str, Any]]:
    """Add event overlay to macro delta before scenario shrinkage."""
    overlay = compute_event_overlay(
        macro_factors,
        as_of_day=as_of_day,
        ticker=ticker,
    )
    adjusted = macro_delta_pct + _finite(overlay.get("return_pct"))
    return adjusted, overlay


def news_features_present(macro_factors: dict[str, Any]) -> bool:
    return any(key in macro_factors for key in NEWS_EVENT_FACTOR_KEYS)


def enrich_macro_with_news_features(
    macro_factors: dict[str, Any],
    *,
    as_of_day: str | None = None,
    ticker: str = "NIFTY",
) -> dict[str, Any]:
    """Attach T0-safe news_* keys for overlay / Ridge inference.

    Walk-forward panel rows may already include historical ``news_*`` columns;
    those values win over hub-only recomputation (which returns zeros for dates
    without verified hub records).
    """
    from trade_integrations.dataflows.index_research.news_event_features import (
        NEWS_EVENT_FACTOR_KEYS,
        compute_news_features_for_day,
    )

    day = (as_of_day or datetime.now(timezone.utc).date().isoformat())[:10]
    hub_feats = compute_news_features_for_day(day, ticker=ticker)
    merged = dict(macro_factors)
    for key in NEWS_EVENT_FACTOR_KEYS:
        if key in merged:
            continue
        merged[key] = hub_feats.get(key, 0.0)
    return merged


def overlay_summary_for_ui(ticker: str = "NIFTY") -> dict[str, Any]:
    config = load_news_model_config(ticker)
    coverage_table = load_shock_calibration(ticker) or {}
    return {
        "news_event_features_status": config.get("news_event_features"),
        "news_event_overlay_status": config.get("news_event_overlay"),
        "calibration_as_of": coverage_table.get("as_of"),
        "reconciled_total": coverage_table.get("reconciled_total"),
        "topics": coverage_table.get("topics") or {},
    }
