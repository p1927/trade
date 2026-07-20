"""Walk-forward track backtest — per-track eval rows + scoreboard."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.backtest_runner import (
    _forward_return_pct,
    load_aligned_factor_history,
)
from trade_integrations.dataflows.index_research.constituent_backtest import (
    load_constituent_signals_for_day,
)
from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.prediction_algorithms.combiners._math import (
    select_alignment_lambda,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.combiners import run_combiner
from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.legacy_replay import (
    replay_legacy_headline,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import (
    normalize_scoreboard_report,
    save_scoreboard,
    summarize_track_metrics,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.promotion import (
    finalize_scoreboard_promotion,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.registry import run_all_tracks
from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
    BACKTEST_COMBINER_IDS,
    CANONICAL_TRACK_IDS,
    COMBINER_THREE_TRACK_IDS,
    EXPERIMENTAL_TRACK_IDS,
    INVERSE_MAE_WINDOWS,
    ML_SEQUENTIAL_TRACK_IDS,
    ML_TABULAR_TRACK_IDS,
    SCOREBOARD_SCHEMA_VERSION,
    TRACK_BACKTEST_ELIGIBLE,
    debate_backtest_eligible,
    walk_forward_track_ids,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.context_builder import build_track_context
from trade_integrations.dataflows.index_research.views import classify_index_view

logger = logging.getLogger(__name__)

_WALK_FORWARD_TRACK_IDS = walk_forward_track_ids()  # default; overridden per run with ticker
_COMBINER_IDS = BACKTEST_COMBINER_IDS


def _row_factor_dict(row: pd.Series, feature_cols: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for col in feature_cols:
        val = row.get(col)
        if pd.notna(val):
            try:
                out[col] = float(val)
            except (TypeError, ValueError):
                continue
    return out


def _append_eval_row(
    eval_rows: list[dict[str, Any]],
    *,
    day_str: str,
    track_id: str,
    predicted_pct: float,
    actual_f: float,
    close: float,
) -> None:
    err = predicted_pct - actual_f
    implied_level = close * (1.0 + predicted_pct / 100.0) if close > 0 else None
    pred_view = classify_index_view(predicted_pct)
    actual_view = classify_index_view(actual_f)
    eval_rows.append(
        {
            "date": day_str,
            "track_id": track_id,
            "predicted_pct": round(predicted_pct, 4),
            "actual_pct": round(actual_f, 4),
            "error_pct": round(err, 4),
            "direction_hit": (predicted_pct > 0) == (actual_f > 0),
            "view_hit": pred_view == actual_view,
            "close": round(close, 2),
            "implied_level": round(implied_level, 2) if implied_level is not None else None,
        }
    )


def run_track_walk_forward(
    *,
    ticker: str = "NIFTY",
    days: int = 365,
    horizon_days: int | None = None,
    min_train_rows: int = 40,
    eval_step: int = 5,
) -> dict[str, Any]:
    """Nested walk-forward per track using aligned factor history."""
    try:
        from sklearn.linear_model import Ridge  # noqa: F401
    except ImportError:
        return {"status": "error", "message": "sklearn required for track backtest"}

    horizon = resolve_horizon(horizon_days)
    frame = load_aligned_factor_history(days=days, start="2006-01-01" if days > 730 else None)
    if frame is None or frame.empty:
        return {"status": "error", "message": "no_factor_history"}

    frame = frame.sort_values("date").reset_index(drop=True)
    frame["target"] = _forward_return_pct(frame["close"].astype(float), horizon.days)
    closes = frame["close"].astype(float)
    frame["realized_1d_pct"] = (closes - closes.shift(1)) / closes.shift(1) * 100.0
    nifty_series = [
        {
            "date": str(row["date"])[:10],
            "close": round(float(row["close"]), 2),
            "realized_1d_pct": round(float(row["realized_1d_pct"]), 3)
            if pd.notna(row["realized_1d_pct"])
            else None,
        }
        for _, row in frame.iterrows()
    ]
    history_start = str(frame["date"].iloc[0])[:10]
    history_end = str(frame["date"].iloc[-1])[:10]

    feature_cols = [
        c
        for c in frame.columns
        if c not in {"date", "close", "target"}
        and pd.api.types.is_numeric_dtype(frame[c])
    ]

    from trade_integrations.dataflows.index_research.predictor import train_macro_ridge
    from trade_integrations.dataflows.index_research.scenarios import (
        build_index_scenarios,
        scenario_weighted_return_pct,
    )

    max_i = len(frame) - horizon.days - 1
    indices = list(range(min_train_rows, max_i + 1, max(1, eval_step)))
    eval_rows: list[dict[str, Any]] = []
    hybrid_eval_dates = 0
    combiner_weight_history: list[dict[str, Any]] = []
    track_ids = list(walk_forward_track_ids(ticker=ticker))

    for i in indices:
        train = frame.iloc[:i].copy()
        row = frame.iloc[i]
        actual = row.get("target")
        if pd.isna(actual):
            continue

        day_str = str(row["date"])[:10]
        close = float(row["close"])
        factors_today = _row_factor_dict(row, feature_cols)
        actual_f = float(actual)

        try:
            artifact = train_macro_ridge(train, horizon)
        except (ValueError, ImportError) as exc:
            logger.debug("track backtest skip train at %s: %s", i, exc)
            continue

        scenarios = []
        scenario_anchor = None
        try:
            signals_for_scenarios = load_constituent_signals_for_day(day_str, factors_today)
            scenarios = build_index_scenarios(
                signals_for_scenarios,
                factors_today,
                spot=close,
                horizon_days=horizon.days,
            )
            scenario_anchor = scenario_weighted_return_pct(scenarios, spot=close)
        except Exception:
            scenarios = []
            signals_for_scenarios = []

        signals = signals_for_scenarios
        if signals and signals[0].symbol != "_INDEX_SENTIMENT":
            hybrid_eval_dates += 1

        legacy_pred = replay_legacy_headline(
            spot=close,
            signals=signals,
            macro_factors=factors_today,
            scenarios=scenarios,
            scenario_anchor=scenario_anchor,
            horizon=horizon,
            model_artifact=artifact,
            as_of_day=day_str,
        )

        debate_payload = None
        try:
            from trade_integrations.context.hub import load_agent_debate_json

            debate_payload = load_agent_debate_json(ticker.strip().upper(), as_of_day=day_str)
        except Exception:
            debate_payload = None

        ctx = build_track_context(
            ticker=ticker,
            spot=close,
            horizon_days=horizon.days,
            macro_factors=factors_today,
            signals=signals,
            scenarios=scenarios,
            scenario_anchor=scenario_anchor,
            debate_payload=debate_payload,
            as_of_day=day_str,
            legacy_prediction=legacy_pred,
        )
        ctx.model_artifact = artifact

        tracks = run_all_tracks(ctx, track_ids=track_ids)

        for track_id, track in tracks.items():
            if not track.available:
                continue
            _append_eval_row(
                eval_rows,
                day_str=day_str,
                track_id=track_id,
                predicted_pct=float(track.expected_return_pct),
                actual_f=actual_f,
                close=close,
            )

        mae_by_track_w6 = {
            tid: summarize_track_metrics(
                eval_rows,
                tid,
                window=INVERSE_MAE_WINDOWS["inverse_mae_w6"],
                before_date=day_str,
            ).get("mae_pct")
            or 1.0
            for tid in COMBINER_THREE_TRACK_IDS
        }
        mae_by_track_w12 = {
            tid: summarize_track_metrics(
                eval_rows,
                tid,
                window=INVERSE_MAE_WINDOWS["inverse_mae_w12"],
                before_date=day_str,
            ).get("mae_pct")
            or 1.0
            for tid in COMBINER_THREE_TRACK_IDS
        }
        alignment_lam = select_alignment_lambda(eval_rows, before_date=day_str)
        mae_ml_pool = ["quant_ridge", *ML_TABULAR_TRACK_IDS, *ML_SEQUENTIAL_TRACK_IDS]
        mae_by_track_ml = {
            tid: summarize_track_metrics(
                eval_rows,
                tid,
                window=INVERSE_MAE_WINDOWS["inverse_mae_w6"],
                before_date=day_str,
            ).get("mae_pct")
            or 1.0
            for tid in mae_ml_pool
        }
        cause_stress = None
        try:
            from trade_integrations.dataflows.index_research.prediction_algorithms.causes.cause_stress_index import (
                compute_cause_stress_index,
            )

            cause_stress = compute_cause_stress_index(factors_today).get("cause_stress_index")
        except Exception:
            cause_stress = None

        for combiner_id in _COMBINER_IDS:
            mae = None
            if combiner_id in INVERSE_MAE_WINDOWS:
                mae = mae_by_track_w6 if combiner_id == "inverse_mae_w6" else mae_by_track_w12
            elif combiner_id == "shrinkage_50":
                mae = mae_by_track_w6
            elif combiner_id in ("stacked_ridge_meta", "equal_weight_ml_3"):
                mae = mae_by_track_ml
            combined = run_combiner(
                combiner_id,
                tracks,
                mae_by_track=mae,
                cause_stress_index=cause_stress,
                lam=alignment_lam if combiner_id == "alignment_grid" else None,
            )
            if combined.weights:
                combiner_weight_history.append(
                    {
                        "date": day_str,
                        "combiner_id": combiner_id,
                        "weights": dict(combined.weights),
                    }
                )
            _append_eval_row(
                eval_rows,
                day_str=day_str,
                track_id=f"combiner:{combiner_id}",
                predicted_pct=float(combined.expected_return_pct),
                actual_f=actual_f,
                close=close,
            )

    primary_dates = {
        r["date"]
        for r in eval_rows
        if not str(r.get("track_id", "")).startswith("combiner:")
    }

    track_summary = {tid: summarize_track_metrics(eval_rows, tid) for tid in CANONICAL_TRACK_IDS}
    for tid in EXPERIMENTAL_TRACK_IDS:
        if tid in track_ids:
            row = summarize_track_metrics(eval_rows, tid)
            row["backtest_eligible"] = TRACK_BACKTEST_ELIGIBLE.get(tid, False)
            row["experimental"] = True
            track_summary[tid] = row
    debate_eligible = debate_backtest_eligible(ticker)
    for tid, row in track_summary.items():
        eligible = TRACK_BACKTEST_ELIGIBLE.get(tid, False)
        if tid == "debate_numeric":
            eligible = debate_eligible
            row["live_only"] = not debate_eligible
            row.setdefault(
                "note",
                "No historical debate archive — live hub only"
                if not debate_eligible
                else "Walk-forward uses agent_debate/history/{date}.json",
            )
        row["backtest_eligible"] = eligible
    combiner_summary = {
        cid: summarize_track_metrics(eval_rows, f"combiner:{cid}") for cid in _COMBINER_IDS
    }

    from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.chart_series import (
        build_track_chart_payload,
    )

    chart = build_track_chart_payload(
        eval_rows,
        nifty_series=nifty_series,
        horizon_days=horizon.days,
    )

    report = {
        "status": "ok",
        "schema_version": SCOREBOARD_SCHEMA_VERSION,
        "ticker": ticker.strip().upper(),
        "horizon_days": horizon.days,
        "history_days": days,
        "history_start": history_start,
        "history_end": history_end,
        "history_rows": len(frame),
        "eval_count": len(primary_dates),
        "hybrid_eval_count": hybrid_eval_dates,
        "tracks": track_summary,
        "combiners": combiner_summary,
        "daily_evaluations": eval_rows,
        "nifty_series": nifty_series[-400:],
        "chart": chart,
        "combiner_weight_history": combiner_weight_history[-300:],
        "limitations": [],
    }
    if hybrid_eval_dates == 0:
        report["limitations"].append(
            "bottom_up uses index_sentiment proxy when company_research/history archives are sparse"
        )
    try:
        from trade_integrations.dataflows.index_research.news_shock_calibration import load_shock_calibration

        shock = load_shock_calibration(ticker) or {}
        if not (shock.get("topics") or {}):
            report["limitations"].append(
                "event_overlay track skipped in walk-forward — news shock calibration has no topics yet"
            )
    except Exception:
        pass
    final_report = finalize_scoreboard_promotion(normalize_scoreboard_report(report), ticker=ticker)
    save_scoreboard(ticker, final_report)
    return final_report
