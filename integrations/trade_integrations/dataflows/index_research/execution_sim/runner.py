"""Execution backtest orchestrator — reads scoreboard eval rows."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.execution_sim.backtrader_strategies import (
    run_backtrader_futures_trend,
)
from trade_integrations.dataflows.index_research.execution_sim.costs import (
    bull_call_spread_charges,
    nifty_futures_round_trip_charges,
)
from trade_integrations.dataflows.index_research.execution_sim.signal_from_track import (
    build_signals_from_eval_rows,
)
from trade_integrations.dataflows.index_research.execution_sim.vectorbt_sweep import sweep_confidence_thresholds
from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import load_scoreboard


def execution_backtest_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "execution_backtest_latest.json"


def run_execution_backtest(
    *,
    ticker: str = "NIFTY",
    track_id: str = "quant_ridge",
    strategy: str = "futures_trend",
    persist: bool = True,
) -> dict[str, Any]:
    board = load_scoreboard(ticker)
    if not board:
        return {"status": "error", "message": "scoreboard_missing"}

    eval_rows = board.get("daily_evaluations") or []
    if not eval_rows:
        return {"status": "error", "message": "no_daily_evaluations"}

    sweep = sweep_confidence_thresholds(eval_rows, track_id=track_id, strategy=strategy)
    best_thr = sweep[0]["threshold"] if sweep else 0.5
    signals = build_signals_from_eval_rows(
        eval_rows,
        track_id=track_id,
        strategy=strategy,  # type: ignore[arg-type]
        threshold=best_thr,
    )
    avg_close = sum(float(s.get("close") or 24000) for s in signals) / max(len(signals), 1)
    futures_charges = nifty_futures_round_trip_charges(price=avg_close)
    spread_charges = bull_call_spread_charges(
        long_strike_price=avg_close * 0.01,
        short_strike_price=avg_close * 0.015,
    )
    bt_result = run_backtrader_futures_trend(signals)

    report: dict[str, Any] = {
        "status": "ok",
        "ticker": ticker.strip().upper(),
        "track_id": track_id,
        "strategy": strategy,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "eval_rows": len(signals),
        "threshold_sweep": sweep,
        "best_threshold": best_thr,
        "signals_sample": signals[:5],
        "charges": {
            "nifty_futures_round_trip": futures_charges,
            "bull_call_spread_proxy": spread_charges,
        },
        "backtrader": bt_result,
    }

    if persist:
        path = execution_backtest_path(ticker)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["artifact_path"] = str(path)

    return report
