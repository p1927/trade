"""Evaluate watch rules against live quote snapshots."""

from __future__ import annotations

from nautilus_openalgo_bridge.models import (
    BridgeSignal,
    QuoteSnapshot,
    WatchAlert,
    WatchRule,
    WatchSpec,
)


def _move_pct(baseline: float, current: float) -> float:
    if baseline == 0:
        return 0.0
    return ((current - baseline) / baseline) * 100.0


def evaluate_rule(
    rule: WatchRule,
    quote: QuoteSnapshot,
    *,
    baseline_ltp: float | None = None,
    baseline_oi: float | None = None,
    baseline_volume: float | None = None,
) -> WatchAlert | None:
    base = baseline_ltp if baseline_ltp is not None else rule.baseline_ltp
    ltp = quote.ltp

    if rule.metric == "spot_move_pct":
        if base is None or base <= 0:
            return None
        move = _move_pct(base, ltp)
        abs_move = abs(move)
        if abs_move < rule.threshold:
            return None
        if rule.direction == "up" and move < 0:
            return None
        if rule.direction == "down" and move > 0:
            return None
        label = rule.label or rule.symbol
        return WatchAlert(
            signal=BridgeSignal.REVIEW_NEEDED,
            rule=rule,
            symbol=rule.symbol,
            message=f"{label} moved {move:+.2f}% (threshold {rule.threshold}%)",
            ltp=ltp,
            move_pct=move,
        )

    if rule.metric == "level_above":
        if ltp <= rule.threshold:
            return None
        label = rule.label or rule.symbol
        return WatchAlert(
            signal=BridgeSignal.REVIEW_NEEDED,
            rule=rule,
            symbol=rule.symbol,
            message=f"{label} LTP {ltp:.2f} above {rule.threshold:.2f}",
            ltp=ltp,
        )

    if rule.metric == "level_below":
        if ltp >= rule.threshold:
            return None
        label = rule.label or rule.symbol
        return WatchAlert(
            signal=BridgeSignal.REVIEW_NEEDED,
            rule=rule,
            symbol=rule.symbol,
            message=f"{label} LTP {ltp:.2f} below {rule.threshold:.2f}",
            ltp=ltp,
        )

    if rule.metric == "oi_change_pct":
        oi_base = baseline_oi if baseline_oi is not None else rule.baseline_ltp
        if oi_base is None or oi_base <= 0 or quote.oi is None:
            return None
        change = _move_pct(oi_base, quote.oi)
        if abs(change) < rule.threshold:
            return None
        if rule.direction == "up" and change < 0:
            return None
        if rule.direction == "down" and change > 0:
            return None
        label = rule.label or rule.symbol
        return WatchAlert(
            signal=BridgeSignal.REVIEW_NEEDED,
            rule=rule,
            symbol=rule.symbol,
            message=f"{label} OI changed {change:+.2f}% (threshold {rule.threshold}%)",
            ltp=ltp,
            move_pct=change,
        )

    if rule.metric == "volume_spike_pct":
        vol_base = baseline_volume if baseline_volume is not None else rule.baseline_ltp
        if vol_base is None or vol_base <= 0 or quote.volume is None:
            return None
        spike = _move_pct(vol_base, quote.volume)
        if spike < rule.threshold:
            return None
        label = rule.label or rule.symbol
        return WatchAlert(
            signal=BridgeSignal.REVIEW_NEEDED,
            rule=rule,
            symbol=rule.symbol,
            message=f"{label} volume spike {spike:+.2f}% (threshold {rule.threshold}%)",
            ltp=ltp,
            move_pct=spike,
        )

    return None


def evaluate_watch_spec(
    spec: WatchSpec,
    quotes: dict[str, QuoteSnapshot],
    *,
    baselines: dict[str, float] | None = None,
    oi_baselines: dict[str, float] | None = None,
    volume_baselines: dict[str, float] | None = None,
) -> list[WatchAlert]:
    """Return alerts for all rules that fire on this poll tick."""
    baselines = baselines or {}
    oi_baselines = oi_baselines or {}
    volume_baselines = volume_baselines or {}
    alerts: list[WatchAlert] = []
    for rule in spec.rules:
        quote = quotes.get(rule.symbol) or quotes.get(rule.symbol.upper())
        if quote is None:
            continue
        alert = evaluate_rule(
            rule,
            quote,
            baseline_ltp=baselines.get(rule.symbol),
            baseline_oi=oi_baselines.get(rule.symbol),
            baseline_volume=volume_baselines.get(rule.symbol),
        )
        if alert is not None:
            alerts.append(alert)
    return alerts
