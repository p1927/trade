"""Market regime classification for index research."""

from __future__ import annotations

_BULL_TRENDS = frozenset({"up", "bull", "bullish", "rising"})
_BEAR_TRENDS = frozenset({"down", "bear", "bearish", "falling"})
_SIDEWAYS_TRENDS = frozenset({"flat", "sideways", "neutral", "range"})


def classify_regime(
    *,
    india_vix: float | None,
    nifty_trend_20d: str | None,
) -> dict:
    """Classify Nifty regime from India VIX and 20-day trend."""
    trend = (nifty_trend_20d or "sideways").strip().lower()

    if trend in _BULL_TRENDS:
        label = "bull"
    elif trend in _BEAR_TRENDS:
        label = "bear"
    elif trend in _SIDEWAYS_TRENDS or not trend:
        label = "sideways"
    else:
        label = "sideways"

    if india_vix is not None:
        if india_vix >= 20:
            label = "bear"
        elif india_vix < 13 and label == "sideways":
            label = "bull"
        elif india_vix >= 18 and label == "bull":
            label = "sideways"

    return {
        "label": label,
        "india_vix": india_vix,
        "trend_20d": nifty_trend_20d,
    }
