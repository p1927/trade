"""Convert forecast track eval rows into trading signals."""

from __future__ import annotations

from typing import Any, Literal

StrategyKind = Literal["futures_trend", "mean_reversion", "options_spread"]


def confidence_threshold_from_history(
    eval_rows: list[dict[str, Any]],
    track_id: str,
    *,
    percentile: float = 70.0,
) -> float:
    preds = [abs(float(r.get("predicted_pct") or 0.0)) for r in eval_rows if r.get("track_id") == track_id]
    if not preds:
        return 0.5
    preds.sort()
    idx = min(len(preds) - 1, int(len(preds) * percentile / 100.0))
    return max(preds[idx], 0.01)


def signal_from_prediction(
    predicted_pct: float,
    *,
    strategy: StrategyKind = "futures_trend",
    threshold: float = 0.5,
    macro_fair_value_pct: float | None = None,
) -> int:
    """Return position: +1 long, -1 short, 0 flat."""
    if strategy == "futures_trend":
        if abs(predicted_pct) < threshold:
            return 0
        return 1 if predicted_pct > 0 else -1
    if strategy == "mean_reversion":
        if macro_fair_value_pct is None:
            return 0
        deviation = predicted_pct - macro_fair_value_pct
        if abs(deviation) < threshold:
            return 0
        return -1 if deviation > 0 else 1
    if strategy == "options_spread":
        if abs(predicted_pct) < threshold:
            return 0
        return 1 if predicted_pct > 0 else -1
    return 0


def build_signals_from_eval_rows(
    eval_rows: list[dict[str, Any]],
    *,
    track_id: str = "quant_ridge",
    macro_track_id: str = "macro_only",
    strategy: StrategyKind = "futures_trend",
    threshold: float | None = None,
) -> list[dict[str, Any]]:
    """Build dated signal rows aligned to scoreboard eval history."""
    thr = threshold if threshold is not None else confidence_threshold_from_history(eval_rows, track_id)
    macro_by_date = {
        str(r.get("date"))[:10]: float(r.get("predicted_pct") or 0.0)
        for r in eval_rows
        if r.get("track_id") == macro_track_id
    }
    out: list[dict[str, Any]] = []
    for row in eval_rows:
        if row.get("track_id") != track_id:
            continue
        day = str(row.get("date") or "")[:10]
        pred = float(row.get("predicted_pct") or 0.0)
        pos = signal_from_prediction(
            pred,
            strategy=strategy,
            threshold=thr,
            macro_fair_value_pct=macro_by_date.get(day),
        )
        out.append(
            {
                "date": day,
                "track_id": track_id,
                "strategy": strategy,
                "predicted_pct": pred,
                "actual_pct": float(row.get("actual_pct") or 0.0),
                "position": pos,
                "threshold": thr,
                "close": row.get("close"),
            }
        )
    return out
