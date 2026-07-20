"""Rolling bottom-up coefficient calibration from archived constituent returns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np

from trade_integrations.dataflows.index_research.attribution import (
    _EARNINGS_BUMP_PCT,
    _EXPECTED_RETURN_CAP_PCT,
    _MOMENTUM_BLEND,
    _MOMENTUM_SCALE,
    _SENTIMENT_BETA,
    _SENTIMENT_BLEND,
)
from trade_integrations.dataflows.index_research.constituent_backtest import (
    bottom_up_return_from_archives,
    load_constituent_signals_for_day,
)

_DEFAULT_WINDOW_DAYS = 60
_MIN_SAMPLES = 12


@dataclass(frozen=True)
class BottomUpCoeffs:
    sentiment_beta: float = _SENTIMENT_BETA
    momentum_scale: float = _MOMENTUM_SCALE
    sentiment_blend: float = _SENTIMENT_BLEND
    momentum_blend: float = _MOMENTUM_BLEND
    sample_count: int = 0
    calibrated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "sentiment_beta": round(self.sentiment_beta, 4),
            "momentum_scale": round(self.momentum_scale, 4),
            "sentiment_blend": round(self.sentiment_blend, 4),
            "momentum_blend": round(self.momentum_blend, 4),
            "sample_count": self.sample_count,
            "calibrated": self.calibrated,
        }


def _fit_linear(x: np.ndarray, y: np.ndarray) -> float | None:
    if x.size < _MIN_SAMPLES or y.size < _MIN_SAMPLES:
        return None
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    if x.size < _MIN_SAMPLES:
        return None
    denom = float(np.dot(x, x))
    if denom <= 1e-12:
        return None
    coef = float(np.dot(x, y) / denom)
    if not np.isfinite(coef):
        return None
    return coef


def _weighted_signal_features(signals: list) -> tuple[float, float]:
    sent_vals = [float(s.sentiment_score or 0.0) * float(s.weight or 0.0) for s in signals]
    mom_vals = [
        float(s.momentum_7d_pct or 0.0) * float(s.weight or 0.0)
        for s in signals
        if s.momentum_7d_pct is not None
    ]
    sentiment = sum(sent_vals)
    momentum = sum(mom_vals) if mom_vals else 0.0
    return sentiment * _SENTIMENT_BETA, momentum * _MOMENTUM_SCALE


def calibrate_bottom_up_coeffs(
    *,
    as_of: date | None = None,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    horizon_days: int = 14,
) -> BottomUpCoeffs:
    """Fit sentiment/momentum scaling on archived rollups when enough history exists."""
    from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history

    end = as_of or date.today()
    start = end - timedelta(days=max(window_days + horizon_days + 7, 90))
    nifty = load_nifty_history(days=(end - start).days + 14)
    if nifty.empty or "date" not in nifty.columns:
        return BottomUpCoeffs()

    dates = [str(d)[:10] for d in nifty["date"].astype(str).tolist()]
    dates = [d for d in dates if start.isoformat() <= d <= end.isoformat()][-window_days:]

    sentiments: list[float] = []
    momentums: list[float] = []
    targets: list[float] = []

    for day in dates:
        actual = bottom_up_return_from_archives(day, horizon_days=horizon_days)
        if actual is None:
            continue
        signals = load_constituent_signals_for_day(day)
        if not signals:
            continue
        sent_feat, mom_feat = _weighted_signal_features(signals)
        sentiments.append(sent_feat)
        momentums.append(mom_feat)
        targets.append(float(actual))

    if len(targets) < _MIN_SAMPLES:
        return BottomUpCoeffs(sample_count=len(targets))

    sent_arr = np.asarray(sentiments, dtype=float)
    mom_arr = np.asarray(momentums, dtype=float)
    tgt_arr = np.asarray(targets, dtype=float)

    sent_beta = _fit_linear(sent_arr, tgt_arr)
    mom_scale = _fit_linear(mom_arr, tgt_arr) if np.any(np.abs(mom_arr) > 1e-9) else None

    if sent_beta is None and mom_scale is None:
        return BottomUpCoeffs(sample_count=len(targets))

    return BottomUpCoeffs(
        sentiment_beta=max(0.5, min(12.0, sent_beta if sent_beta is not None else _SENTIMENT_BETA)),
        momentum_scale=max(0.05, min(2.0, mom_scale if mom_scale is not None else _MOMENTUM_SCALE)),
        sample_count=len(targets),
        calibrated=True,
    )


def apply_bottom_up_coeffs(
    sentiment_move: float,
    momentum_move: float | None,
    coeffs: BottomUpCoeffs,
    *,
    earnings_bump: bool = False,
) -> float:
    """Expected constituent return using calibrated or default coefficients."""
    sent = sentiment_move * (coeffs.sentiment_beta / _SENTIMENT_BETA)
    mom = (momentum_move * (coeffs.momentum_scale / _MOMENTUM_SCALE)) if momentum_move is not None else None
    if mom is not None:
        raw = coeffs.sentiment_blend * sent + coeffs.momentum_blend * mom
    else:
        raw = sent
    capped = max(-_EXPECTED_RETURN_CAP_PCT, min(_EXPECTED_RETURN_CAP_PCT, raw))
    if earnings_bump:
        capped += _EARNINGS_BUMP_PCT
    return capped
