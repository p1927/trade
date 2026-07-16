"""Trading-session horizon dates aligned with backtest ``close.shift(-n)`` targets."""

from __future__ import annotations


def resolve_maturity_trading_date(
    prediction_date: str,
    horizon_trading_days: int,
    trading_dates: list[str],
) -> str | None:
    """Return ``trading_dates[idx + horizon_trading_days]`` for ``prediction_date``.

    Matches ``factor_matrix._forward_return_pct`` / backtest ``close.shift(-horizon)``.
    """
    if not trading_dates or horizon_trading_days < 1:
        return None
    pred = str(prediction_date).strip()[:10]
    ordered = [str(d).strip()[:10] for d in trading_dates]
    try:
        idx = ordered.index(pred)
    except ValueError:
        eligible = [i for i, d in enumerate(ordered) if d <= pred]
        if not eligible:
            return None
        idx = eligible[-1]
    target_idx = idx + int(horizon_trading_days)
    if target_idx >= len(ordered):
        return None
    return ordered[target_idx]


def trading_dates_from_frame(frame) -> list[str]:
    """Extract sorted YYYY-MM-DD trading dates from aligned history."""
    if frame is None or getattr(frame, "empty", True):
        return []
    if "date" not in frame.columns:
        return []
    return frame["date"].astype(str).str[:10].tolist()
