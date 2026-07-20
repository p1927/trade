"""Constituent → index attribution for the Nifty research pipeline."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

from trade_integrations.dataflows.index_research.models import ConstituentSignal

_SENTIMENT_BETA = 5.0
_MOMENTUM_BLEND = 0.3
_SENTIMENT_BLEND = 0.7
_MOMENTUM_SCALE = 0.5
_EXPECTED_RETURN_CAP_PCT = 3.0
_EARNINGS_BUMP_PCT = 0.5
_EARNINGS_EVENT_TYPES = frozenset({"results", "earnings", "earnings_signal"})


def _today() -> date:
    return date.today()


def _is_earnings_event(event: dict) -> bool:
    event_type = str(event.get("type", "")).lower()
    if event_type in _EARNINGS_EVENT_TYPES:
        return True
    return "result" in event_type or "earning" in event_type


def _parse_event_date(raw: object) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    text = str(raw).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _has_earnings_within_horizon(
    signal: ConstituentSignal,
    *,
    horizon_days: int,
    as_of: date,
) -> bool:
    deadline = as_of + timedelta(days=horizon_days)
    for event in signal.events:
        if not _is_earnings_event(event):
            continue
        event_date = _parse_event_date(event.get("date"))
        if event_date is None:
            return True
        if as_of <= event_date <= deadline:
            return True
    return False


def _expected_return_pct(
    signal: ConstituentSignal,
    *,
    horizon_days: int,
    sentiment_beta: float = _SENTIMENT_BETA,
    momentum_scale: float = _MOMENTUM_SCALE,
    sentiment_blend: float = _SENTIMENT_BLEND,
    momentum_blend: float = _MOMENTUM_BLEND,
) -> float:
    sentiment = signal.sentiment_score or 0.0
    sentiment_move = sentiment * sentiment_beta
    if signal.momentum_7d_pct is not None:
        momentum_move = float(signal.momentum_7d_pct) * momentum_scale
        raw = sentiment_blend * sentiment_move + momentum_blend * momentum_move
    else:
        raw = sentiment_move
    capped = max(-_EXPECTED_RETURN_CAP_PCT, min(_EXPECTED_RETURN_CAP_PCT, raw))
    if _has_earnings_within_horizon(signal, horizon_days=horizon_days, as_of=_today()):
        capped += _EARNINGS_BUMP_PCT
    return capped


def attribute_constituent(
    signal: ConstituentSignal,
    *,
    horizon_days: int = 14,
    sentiment_beta: float = _SENTIMENT_BETA,
    momentum_scale: float = _MOMENTUM_SCALE,
    sentiment_blend: float = _SENTIMENT_BLEND,
    momentum_blend: float = _MOMENTUM_BLEND,
) -> ConstituentSignal:
    """Attribute a single constituent's expected move to index contribution."""
    expected = _expected_return_pct(
        signal,
        horizon_days=horizon_days,
        sentiment_beta=sentiment_beta,
        momentum_scale=momentum_scale,
        sentiment_blend=sentiment_blend,
        momentum_blend=momentum_blend,
    )
    contribution = signal.weight * expected
    return replace(signal, contribution_to_index_pct=contribution)


def attribute_constituents(
    signals: list[ConstituentSignal],
    *,
    horizon_days: int = 14,
    use_calibration: bool = True,
) -> list[ConstituentSignal]:
    """Attribute all constituents and sort by absolute contribution descending."""
    sentiment_beta = _SENTIMENT_BETA
    momentum_scale = _MOMENTUM_SCALE
    sentiment_blend = _SENTIMENT_BLEND
    momentum_blend = _MOMENTUM_BLEND
    if use_calibration and signals:
        try:
            from trade_integrations.dataflows.index_research.calibrate_bottom_up import (
                calibrate_bottom_up_coeffs,
            )

            coeffs = calibrate_bottom_up_coeffs()
            sentiment_beta = coeffs.sentiment_beta
            momentum_scale = coeffs.momentum_scale
            sentiment_blend = coeffs.sentiment_blend
            momentum_blend = coeffs.momentum_blend
        except Exception:
            pass

    attributed = [
        attribute_constituent(
            signal,
            horizon_days=horizon_days,
            sentiment_beta=sentiment_beta,
            momentum_scale=momentum_scale,
            sentiment_blend=sentiment_blend,
            momentum_blend=momentum_blend,
        )
        for signal in signals
    ]
    return sorted(
        attributed,
        key=lambda signal: abs(signal.contribution_to_index_pct or 0.0),
        reverse=True,
    )


def rollup_attribution(signals: list[ConstituentSignal]) -> dict:
    """Roll up total index contribution and ranked top drivers."""
    total = sum(signal.contribution_to_index_pct or 0.0 for signal in signals)
    top_drivers = [
        {
            "symbol": signal.symbol,
            "contribution_to_index_pct": signal.contribution_to_index_pct,
            "weight": signal.weight,
            "sector": signal.sector,
        }
        for signal in sorted(
            signals,
            key=lambda row: abs(row.contribution_to_index_pct or 0.0),
            reverse=True,
        )
    ]
    return {
        "total_contribution_pct": total,
        "top_drivers": top_drivers,
    }
