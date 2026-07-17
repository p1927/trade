"""Pre-specified non-linear flow regime adjustments for macro delta."""

from __future__ import annotations

from typing import Any

_FII_CONTRARIAN_OFFSET_CAP_PCT = 0.5
_DAR_ABSORPTION_THRESHOLD = 1.2
_FII_SELLING_THRESHOLD = 0.0


def apply_flow_regime_adjustment(
    macro_delta_pct: float,
    factors: dict[str, Any],
    regime_label: str,
) -> float:
    """
    Bounded flow regime offsets — not tuned on miss dates.

    range_bound + FII selling → small contrarian bullish offset
    range_bound + DAR > 1.2 → dampen bearish macro contribution
    trend_down → no flow adjustment (FII contrarian disabled via regime gate)
    """
    if regime_label == "trend_down":
        return macro_delta_pct

    adjusted = macro_delta_pct
    fii_5d = _float_or_none(factors.get("fii_net_5d"))
    dar = _float_or_none(factors.get("dii_absorption_ratio"))

    if regime_label == "range_bound" and fii_5d is not None and fii_5d < _FII_SELLING_THRESHOLD:
        # Contrarian: FII selling often absorbed by DII in range markets.
        offset = min(_FII_CONTRARIAN_OFFSET_CAP_PCT, abs(fii_5d) / 5000.0)
        adjusted += offset

    if regime_label == "range_bound" and dar is not None and dar > _DAR_ABSORPTION_THRESHOLD:
        if adjusted < 0:
            adjusted *= 0.5

    if regime_label == "high_fear" and fii_5d is not None and fii_5d < _FII_SELLING_THRESHOLD:
        # Smaller contrarian nudge in high fear — flows stay full weight.
        adjusted += min(0.25, abs(fii_5d) / 8000.0)

    return adjusted


def flow_regime_bucket(factors: dict[str, Any], regime_label: str) -> str:
    """Label eval row for backtest regime-flow reporting."""
    fii_5d = _float_or_none(factors.get("fii_net_5d"))
    dar = _float_or_none(factors.get("dii_absorption_ratio"))
    if regime_label == "trend_down":
        return "trend_down"
    if regime_label == "high_fear":
        return "high_fear"
    if fii_5d is not None and fii_5d < 0 and dar is not None and dar > _DAR_ABSORPTION_THRESHOLD:
        return "range_fii_sell_dii_absorb"
    if fii_5d is not None and fii_5d < 0:
        return "range_fii_selling"
    return "range_bound"


def _float_or_none(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
