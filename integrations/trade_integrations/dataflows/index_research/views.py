"""Shared index prediction view classification from expected return."""

from __future__ import annotations

_VIEW_BULL_THRESHOLD = 0.3
_VIEW_BEAR_THRESHOLD = -0.3


def classify_index_view(expected_return_pct: float) -> str:
    """Map horizon expected return (%) to bullish / bearish / neutral."""
    if expected_return_pct >= _VIEW_BULL_THRESHOLD:
        return "bullish"
    if expected_return_pct <= _VIEW_BEAR_THRESHOLD:
        return "bearish"
    return "neutral"
