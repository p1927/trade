"""vectorbt threshold sweep for execution strategies."""

from __future__ import annotations

from typing import Any

from trade_integrations.dataflows.index_research.execution_sim.signal_from_track import build_signals_from_eval_rows


def sweep_confidence_thresholds(
    eval_rows: list[dict[str, Any]],
    *,
    track_id: str = "quant_ridge",
    strategy: str = "futures_trend",
    thresholds: tuple[float, ...] | None = None,
    lot_size: int = 25,
    lots: int = 1,
) -> list[dict[str, Any]]:
    """Grid search thresholds; uses vectorbt when installed, else numpy fallback."""
    grid = thresholds or (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
    results: list[dict[str, Any]] = []

    try:
        import numpy as np
        import vectorbt as vbt
    except ImportError:
        return _sweep_numpy_fallback(
            eval_rows,
            track_id=track_id,
            strategy=strategy,
            thresholds=grid,
            lot_size=lot_size,
            lots=lots,
        )

    for thr in grid:
        signals = build_signals_from_eval_rows(
            eval_rows,
            track_id=track_id,
            strategy=strategy,  # type: ignore[arg-type]
            threshold=thr,
        )
        if len(signals) < 5:
            continue
        positions = np.array([s["position"] for s in signals], dtype=float)
        returns = np.array([s["actual_pct"] for s in signals], dtype=float) / 100.0
        gross = float(np.nansum(positions * returns))
        from trade_integrations.dataflows.index_research.execution_sim.costs import nifty_futures_round_trip_charges

        avg_close = float(np.nanmean([s.get("close") or 24000 for s in signals]))
        charges = nifty_futures_round_trip_charges(price=avg_close, lots=lots, lot_size=lot_size)
        cost_pct = float(charges["total_charges_inr"]) / max(avg_close * lot_size * lots, 1.0)
        trades = int(np.sum(np.abs(np.diff(np.concatenate([[0], positions]))) > 0))
        per_trade_cost = cost_pct * max(trades, 1)
        net = gross - per_trade_cost
        results.append(
            {
                "threshold": thr,
                "gross_return_pct": round(gross * 100.0, 4),
                "net_return_pct": round(net * 100.0, 4),
                "trades": trades,
                "avg_cost_inr": charges["total_charges_inr"],
                "engine": "vectorbt",
            }
        )
    results.sort(key=lambda r: r.get("net_return_pct") or -999.0, reverse=True)
    return results


def _sweep_numpy_fallback(
    eval_rows: list[dict[str, Any]],
    *,
    track_id: str,
    strategy: str,
    thresholds: tuple[float, ...],
    lot_size: int,
    lots: int,
) -> list[dict[str, Any]]:
    import numpy as np

    from trade_integrations.dataflows.index_research.execution_sim.costs import nifty_futures_round_trip_charges

    results: list[dict[str, Any]] = []
    for thr in thresholds:
        signals = build_signals_from_eval_rows(
            eval_rows,
            track_id=track_id,
            strategy=strategy,  # type: ignore[arg-type]
            threshold=thr,
        )
        if len(signals) < 5:
            continue
        positions = np.array([s["position"] for s in signals], dtype=float)
        returns = np.array([s["actual_pct"] for s in signals], dtype=float) / 100.0
        gross = float(np.nansum(positions * returns))
        avg_close = float(np.nanmean([s.get("close") or 24000 for s in signals]))
        charges = nifty_futures_round_trip_charges(price=avg_close, lots=lots, lot_size=lot_size)
        trades = int(np.sum(np.abs(np.diff(np.concatenate([[0], positions]))) > 0))
        cost_pct = float(charges["total_charges_inr"]) / max(avg_close * lot_size * lots, 1.0)
        net = gross - cost_pct * max(trades, 1)
        results.append(
            {
                "threshold": thr,
                "gross_return_pct": round(gross * 100.0, 4),
                "net_return_pct": round(net * 100.0, 4),
                "trades": trades,
                "avg_cost_inr": charges["total_charges_inr"],
                "engine": "numpy_fallback",
            }
        )
    results.sort(key=lambda r: r.get("net_return_pct") or -999.0, reverse=True)
    return results
