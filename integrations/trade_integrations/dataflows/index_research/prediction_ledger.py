"""Forecast logging and reconciliation for index research."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.hub_storage.parquet_io import concat_dataframes

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.models import PredictionRecord
from trade_integrations.dataflows.index_research.factor_store import load_factor_history
from trade_integrations.dataflows.index_research.factor_matrix import MACRO_FACTOR_KEYS
from trade_integrations.dataflows.index_research.horizon_dates import resolve_maturity_trading_date
from trade_integrations.dataflows.index_research.sources.history_loader import (
    NIFTY_SYMBOL,
    load_nifty_history,
)

_LEDGER_SUBDIR = "_data/index_predictions"
_LEDGER_FILENAME = "ledger.parquet"
_MISS_ANALYSIS_FILENAME = "miss_analysis.parquet"
_NEUTRAL_RETURN_THRESHOLD_PCT = 0.15


def _scenario_ledger_row(scenario: dict[str, Any]) -> dict[str, Any]:
    """Map live scenario dict fields to compact ledger RCA shape."""
    event = str(scenario.get("event") or "").strip()
    outcome = str(scenario.get("outcome") or "").strip()
    name = scenario.get("name") or scenario.get("label")
    if not name and event:
        name = f"{event}: {outcome}" if outcome else event
    expected = scenario.get("expected_return_pct")
    if expected is None:
        expected = scenario.get("midpoint_return_pct")
    return {
        "name": name,
        "probability": scenario.get("probability"),
        "expected_return_pct": expected,
    }


def build_prediction_metadata(
    *,
    ticker: str,
    horizon_name: str,
    refresh: str,
    prediction: dict[str, Any],
    global_factors: list[dict[str, Any]] | None = None,
    regime: dict[str, Any] | None = None,
    scenarios: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compact snapshot for ledger RCA at reconcile time."""
    factor_map: dict[str, float] = {}
    for row in global_factors or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("factor") or "").strip()
        val = row.get("value")
        if key in MACRO_FACTOR_KEYS and val is not None:
            try:
                factor_map[key] = float(val)
            except (TypeError, ValueError):
                continue

    meta: dict[str, Any] = {
        "ticker": ticker.strip().upper(),
        "horizon_name": horizon_name,
        "refresh": refresh,
        "bottom_up_return_pct": float(prediction.get("bottom_up_return_pct") or 0.0),
        "macro_delta_pct": float(prediction.get("macro_delta_pct") or 0.0),
        "direction_view": prediction.get("direction_view"),
        "direction_confidence": prediction.get("direction_confidence"),
        "scenario_anchor_return_pct": prediction.get("scenario_anchor_return_pct"),
        "reconciled_with_scenarios": prediction.get("reconciled_with_scenarios"),
        "raw_expected_return_pct": prediction.get("raw_expected_return_pct"),
        "flow_coverage": prediction.get("flow_coverage"),
        "data_quality_warning": prediction.get("data_quality_warning"),
        "sign_conflict": prediction.get("sign_conflict"),
        "global_factors": factor_map,
    }
    if regime:
        meta["regime"] = {
            k: regime[k]
            for k in ("label", "india_vix", "trend_20d")
            if k in regime
        }
    if scenarios:
        meta["scenarios"] = [
            _scenario_ledger_row(s)
            for s in scenarios[:6]
            if isinstance(s, dict)
        ]
    tracks = prediction.get("forecast_tracks")
    if isinstance(tracks, dict) and tracks:
        meta["forecast_tracks_summary"] = {
            tid: {
                "expected_return_pct": row.get("expected_return_pct"),
                "view": row.get("view"),
            }
            for tid, row in tracks.items()
            if isinstance(row, dict)
        }
    if prediction.get("cause_stress_index") is not None:
        meta["cause_stress_index"] = prediction.get("cause_stress_index")
    return meta


def get_miss_analysis_path() -> Path:
    return get_hub_dir() / _LEDGER_SUBDIR / _MISS_ANALYSIS_FILENAME


def get_ledger_path() -> Path:
    """Return path to the shared prediction ledger parquet file."""
    return get_hub_dir() / _LEDGER_SUBDIR / _LEDGER_FILENAME


def _write_parquet(df: pd.DataFrame, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = path.with_suffix(".csv")
    try:
        df.to_parquet(path, index=False)
    except ImportError:
        df.to_csv(csv_path, index=False)
        return
    # Mirror CSV so readers without pyarrow stay in sync with parquet.
    df.to_csv(csv_path, index=False)


def _read_parquet(path) -> pd.DataFrame:
    csv_path = path.with_suffix(".csv")
    if path.is_file():
        try:
            return pd.read_parquet(path)
        except (ImportError, Exception):
            if csv_path.is_file():
                return pd.read_csv(csv_path)
    if csv_path.is_file():
        return pd.read_csv(csv_path)
    return pd.DataFrame()


def _record_to_row(record: PredictionRecord) -> dict[str, Any]:
    metadata = record.metadata or {}
    return {
        "predicted_at": record.predicted_at.isoformat(),
        "horizon_days": int(record.horizon_days),
        "spot_at_prediction": float(record.spot_at_prediction),
        "expected_return_pct": float(record.expected_return_pct),
        "range_low": float(record.range_low),
        "range_high": float(record.range_high),
        "actual_return_pct": record.actual_return_pct,
        "direction_correct": record.direction_correct,
        "metadata_json": json.dumps(metadata, default=str),
    }


def _parse_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_to_record(row: pd.Series) -> PredictionRecord:
    predicted_at = row["predicted_at"]
    if not isinstance(predicted_at, datetime):
        predicted_at = datetime.fromisoformat(str(predicted_at))
    if predicted_at.tzinfo is None:
        predicted_at = predicted_at.replace(tzinfo=timezone.utc)

    actual = row.get("actual_return_pct")
    direction = row.get("direction_correct")
    metadata = _parse_metadata(row.get("metadata_json", row.get("metadata")))

    return PredictionRecord(
        predicted_at=predicted_at,
        horizon_days=int(row["horizon_days"]),
        spot_at_prediction=float(row["spot_at_prediction"]),
        expected_return_pct=float(row["expected_return_pct"]),
        range_low=float(row["range_low"]),
        range_high=float(row["range_high"]),
        actual_return_pct=float(actual) if pd.notna(actual) else None,
        direction_correct=bool(direction) if pd.notna(direction) else None,
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def load_ledger() -> pd.DataFrame:
    """Load the prediction ledger as a DataFrame."""
    return _read_parquet(get_ledger_path())


def save_ledger(df: pd.DataFrame) -> None:
    """Persist the prediction ledger."""
    _write_parquet(df, get_ledger_path())


def append_prediction(record: PredictionRecord) -> None:
    """Append a forecast row to the shared ledger."""
    row = _record_to_row(record)
    existing = load_ledger()
    updated = concat_dataframes(existing, pd.DataFrame([row]))
    save_ledger(updated)


def _direction_correct(expected_return_pct: float, actual_return_pct: float) -> bool:
    if abs(expected_return_pct) < _NEUTRAL_RETURN_THRESHOLD_PCT:
        return abs(actual_return_pct) < _NEUTRAL_RETURN_THRESHOLD_PCT
    return (expected_return_pct >= 0) == (actual_return_pct >= 0)


def _close_on_or_before(history: pd.DataFrame, target: date) -> float | None:
    if history.empty:
        return None
    frame = history.copy()
    frame["day"] = pd.to_datetime(frame["date"]).dt.date
    eligible = frame[frame["day"] <= target]
    if eligible.empty:
        return None
    return float(eligible.iloc[-1]["close"])


def _fetch_nifty_close_on(target: date, *, history: pd.DataFrame | None = None) -> float | None:
    frame = history
    if frame is None or frame.empty:
        start = target - timedelta(days=14)
        end = target + timedelta(days=3)
        import yfinance as yf

        hist = yf.Ticker(NIFTY_SYMBOL).history(
            start=start.isoformat(),
            end=end.isoformat(),
        )
        if hist.empty:
            return None
        hist = hist.reset_index()
        date_col = "Date" if "Date" in hist.columns else hist.columns[0]
        close_col = "Close" if "Close" in hist.columns else "close"
        frame = pd.DataFrame(
            {
                "date": pd.to_datetime(hist[date_col]).dt.strftime("%Y-%m-%d"),
                "close": hist[close_col].astype(float),
            }
        )
    return _close_on_or_before(frame, target)


def reconcile_predictions(*, as_of: datetime | None = None) -> int:
    """Fill actual returns for matured predictions using yfinance Nifty history."""
    now = as_of or datetime.now(timezone.utc)
    today = now.date()
    ledger = load_ledger()
    if ledger.empty:
        return 0

    history = load_nifty_history(days=400)
    trading_dates = history["date"].astype(str).str[:10].tolist() if not history.empty else []
    updated = 0

    for idx, row in ledger.iterrows():
        if pd.notna(row.get("actual_return_pct")):
            continue

        predicted_at = row["predicted_at"]
        if not isinstance(predicted_at, datetime):
            predicted_at = datetime.fromisoformat(str(predicted_at))
        pred_date = predicted_at.date() if hasattr(predicted_at, "date") else predicted_at
        horizon_days = int(row["horizon_days"])
        pred_day = pred_date.isoformat() if hasattr(pred_date, "isoformat") else str(pred_date)[:10]
        maturity_day = resolve_maturity_trading_date(pred_day, horizon_days, trading_dates)
        if not maturity_day:
            continue
        try:
            maturity = date.fromisoformat(maturity_day[:10])
        except ValueError:
            continue
        if maturity > today:
            continue

        spot = float(row["spot_at_prediction"])
        actual_close = _fetch_nifty_close_on(maturity, history=history)
        if actual_close is None or spot <= 0:
            continue

        actual_return_pct = (actual_close - spot) / spot * 100.0
        expected = float(row["expected_return_pct"])
        ledger.at[idx, "actual_return_pct"] = actual_return_pct
        ledger.at[idx, "direction_correct"] = _direction_correct(expected, actual_return_pct)
        updated += 1

    if updated:
        save_ledger(ledger)
        _persist_ledger_miss_analysis(ledger)
    return updated


def _persist_ledger_miss_analysis(ledger: pd.DataFrame) -> None:
    """Write RCA rows for reconciled ledger misses."""
    if ledger.empty:
        return
    reconciled = ledger[ledger["actual_return_pct"].notna()].copy()
    if reconciled.empty:
        return

    rows: list[dict[str, Any]] = []
    for _, row in reconciled.iterrows():
        record = _row_to_record(row)
        if record.direction_correct is not False:
            continue
        rows.append(
            {
                "predicted_at": record.predicted_at.isoformat(),
                "horizon_days": record.horizon_days,
                "expected_return_pct": record.expected_return_pct,
                "actual_return_pct": record.actual_return_pct,
                "direction_correct": record.direction_correct,
                "metadata": record.metadata or {},
            }
        )
    if not rows:
        return

    try:
        from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
            analyze_ledger_misses,
        )

        analyses = analyze_ledger_misses(rows)
        if analyses:
            _write_parquet(pd.DataFrame(analyses), get_miss_analysis_path())
        try:
            from trade_integrations.dataflows.index_research.prediction_counterfactual import (
                analyze_ledger_counterfactual,
            )

            cf_rows = analyze_ledger_counterfactual(rows)
            if cf_rows:
                cf_path = get_hub_dir() / _LEDGER_SUBDIR / "ledger_counterfactual_latest.json"
                cf_path.parent.mkdir(parents=True, exist_ok=True)
                cf_path.write_text(
                    json.dumps({"rows": cf_rows, "as_of": datetime.now(timezone.utc).isoformat()}, indent=2),
                    encoding="utf-8",
                )
        except Exception:
            pass
    except Exception:
        pass


def list_factor_history_series(
    *,
    days: int = 90,
    start: str | None = None,
    factors: list[str] | None = None,
    include_nifty_close: bool = True,
) -> dict[str, Any]:
    """Return wide-format daily factor + optional Nifty close series for charting."""
    from datetime import timedelta

    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.dataflows.index_research.history_panel import load_aligned_panel_history
    from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history

    end = date.fromisoformat(india_trading_date_iso()[:10])
    max_days = 5000
    window_days = max(1, min(days, max_days))
    if start:
        start_date = date.fromisoformat(start[:10])
    else:
        start_date = end - timedelta(days=window_days)

    panel = load_aligned_panel_history(days=0, start=start_date.isoformat())
    series: dict[str, dict[str, float]] = {}

    if panel is not None and not panel.empty:
        panel = panel[panel["date"] >= start_date.isoformat()]
        panel = panel[panel["date"] <= end.isoformat()]
        for _, row in panel.iterrows():
            day = str(row["date"])[:10]
            bucket = series.setdefault(day, {})
            for col in panel.columns:
                if col == "date":
                    continue
                if factors and col not in factors:
                    continue
                val = row[col]
                if pd.isna(val):
                    continue
                try:
                    bucket[col] = float(val)
                except (TypeError, ValueError):
                    continue
        if include_nifty_close:
            for day, bucket in series.items():
                if "nifty_close" in bucket and "close" not in bucket:
                    bucket["nifty_close"] = bucket.get("close") or bucket["nifty_close"]
                if "close" in bucket:
                    bucket["nifty_close"] = bucket["close"]

    if not series:
        long_df = load_factor_history(start_date.isoformat(), end.isoformat())
        if not long_df.empty and "factor" in long_df.columns:
            value_col = "value" if "value" in long_df.columns else long_df.columns[-1]
            for _, row in long_df.iterrows():
                day = str(row.get("date", ""))[:10]
                factor = str(row.get("factor", ""))
                if not day or not factor:
                    continue
                if factors and factor not in factors:
                    continue
                try:
                    series.setdefault(day, {})[factor] = float(row[value_col])
                except (TypeError, ValueError):
                    continue

        if include_nifty_close:
            nifty = load_nifty_history(days=window_days + 5, start=start_date.isoformat())
            for _, row in nifty.iterrows():
                day = str(row["date"])[:10]
                if start_date.isoformat() <= day <= end.isoformat():
                    series.setdefault(day, {})["nifty_close"] = float(row["close"])

    ordered_days = sorted(series.keys())
    rows = [{"date": day, **series[day]} for day in ordered_days]
    factor_names = sorted({k for day in rows for k in day if k != "date"})
    coverage = {
        factor: sum(1 for row in rows if row.get(factor) is not None)
        for factor in factor_names
    }
    coverage_notes: list[str] = []
    if "fii_net_5d" in coverage and coverage["fii_net_5d"] < len(rows) * 0.5:
        coverage_notes.append(
            "FII/DII cash flows: ~111 trading days via Mr. Chartist (NSE has no free historical JSON)."
        )
    if "nifty_pcr" in coverage and coverage["nifty_pcr"] < len(rows) * 0.5:
        coverage_notes.append(
            "PCR / FII OI: backfilled from nselib participant OI (run backfill_participant_oi for full range)."
        )
    return {
        "series": rows,
        "factors": factor_names,
        "start": start_date.isoformat(),
        "end": end.isoformat(),
        "coverage": coverage,
        "coverage_notes": coverage_notes,
    }


def list_prediction_history(
    ticker: str = "NIFTY",
    *,
    limit: int = 50,
    horizon_days: int | None = None,
    daily_last: bool = True,
) -> list[dict[str, Any]]:
    """Return recent ledger rows for one index ticker (newest first)."""
    sym = ticker.strip().upper()
    ledger = load_ledger()
    if ledger.empty:
        return []

    parsed: list[dict[str, Any]] = []
    for _, row in ledger.iloc[::-1].iterrows():
        record = _row_to_record(row)
        meta = record.metadata or {}
        row_ticker = str(meta.get("ticker") or "NIFTY").strip().upper()
        if row_ticker != sym:
            continue
        if horizon_days is not None and record.horizon_days != horizon_days:
            continue
        spot = float(record.spot_at_prediction)
        expected = float(record.expected_return_pct)
        implied_level = spot * (1.0 + expected / 100.0)
        parsed.append(
            {
                "predicted_at": record.predicted_at.isoformat(),
                "horizon_days": record.horizon_days,
                "spot_at_prediction": spot,
                "expected_return_pct": expected,
                "implied_level": implied_level,
                "range_low": float(record.range_low),
                "range_high": float(record.range_high),
                "actual_return_pct": record.actual_return_pct,
                "direction_correct": record.direction_correct,
                "horizon_name": meta.get("horizon_name"),
                "bottom_up_return_pct": meta.get("bottom_up_return_pct"),
                "macro_delta_pct": meta.get("macro_delta_pct"),
                "refresh": meta.get("refresh"),
            }
        )

    if daily_last and parsed:
        by_day: dict[str, dict[str, Any]] = {}
        for row in parsed:
            day = row["predicted_at"][:10]
            if day not in by_day:
                by_day[day] = row
        parsed = list(by_day.values())
        parsed.sort(key=lambda r: r["predicted_at"], reverse=True)

    return parsed[: max(1, limit)]


def _snapshot_to_history_row(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    as_of = snapshot.get("as_of")
    spot = snapshot.get("spot")
    expected = snapshot.get("expected_return_pct")
    if not as_of or spot is None or expected is None:
        return None
    try:
        spot_f = float(spot)
        expected_f = float(expected)
    except (TypeError, ValueError):
        return None
    range_low = snapshot.get("range_low")
    range_high = snapshot.get("range_high")
    if range_low is None or range_high is None:
        range_low = spot_f * (1.0 + expected_f / 100.0 - 0.015)
        range_high = spot_f * (1.0 + expected_f / 100.0 + 0.015)
    return {
        "predicted_at": str(as_of),
        "horizon_days": int(snapshot.get("horizon_days") or 14),
        "spot_at_prediction": spot_f,
        "expected_return_pct": expected_f,
        "implied_level": spot_f * (1.0 + expected_f / 100.0),
        "range_low": float(range_low),
        "range_high": float(range_high),
        "actual_return_pct": None,
        "direction_correct": None,
        "horizon_name": None,
        "bottom_up_return_pct": snapshot.get("bottom_up_return_pct"),
        "macro_delta_pct": snapshot.get("macro_delta_pct"),
        "refresh": "snapshot",
    }


def list_forecast_history_bundle(
    ticker: str = "NIFTY",
    *,
    limit: int = 90,
    horizon_days: int | None = None,
) -> dict[str, Any]:
    """Daily forecast series for charts plus optional intraday revisions for today."""
    from trade_integrations.dataflows.index_research.snapshots import list_index_research_snapshots

    sym = ticker.strip().upper()
    intraday = list_prediction_history(
        sym,
        limit=max(1, limit),
        horizon_days=horizon_days,
        daily_last=False,
    )
    ledger_daily = list_prediction_history(
        sym,
        limit=max(1, limit),
        horizon_days=horizon_days,
        daily_last=True,
    )

    by_day: dict[str, dict[str, Any]] = {}
    for row in ledger_daily:
        day = row["predicted_at"][:10]
        by_day[day] = row

    for snapshot in list_index_research_snapshots(sym, limit=limit):
        if horizon_days is not None and snapshot.get("horizon_days") not in (None, horizon_days):
            continue
        row = _snapshot_to_history_row(snapshot)
        if not row:
            continue
        day = row["predicted_at"][:10]
        if day not in by_day:
            by_day[day] = row

    daily = sorted(by_day.values(), key=lambda r: r["predicted_at"])
    unique_days = len(by_day)

    from trade_integrations.dataflows.company_research.market import india_trading_date_iso

    today = india_trading_date_iso()[:10]
    intraday_today = [r for r in intraday if str(r.get("predicted_at", ""))[:10] == today]
    if not intraday_today and unique_days == 1 and len(intraday) > 1:
        intraday_today = intraday

    return {
        "daily": daily,
        "intraday": intraday_today,
        "meta": {
            "unique_days": unique_days,
            "intraday_revisions": len(intraday_today),
            "granularity": "daily",
            "needs_more_days": unique_days < 2,
        },
        "intraday_days": sorted({str(r.get("predicted_at", ""))[:10] for r in intraday}),
    }


def compute_accuracy_metrics(*, window: int = 14) -> dict[str, Any]:
    """Compute rolling MAE and direction hit rate from reconciled ledger rows."""
    ledger = load_ledger()
    if ledger.empty or "actual_return_pct" not in ledger.columns:
        return {
            "sample_count": 0,
            "mae_pct": None,
            "mae_14d_pct": None,
            "direction_hit_rate": None,
            "direction_hit_rate_14d": None,
        }

    reconciled = ledger[ledger["actual_return_pct"].notna()].copy()
    if reconciled.empty:
        return {
            "sample_count": 0,
            "mae_pct": None,
            "mae_14d_pct": None,
            "direction_hit_rate": None,
            "direction_hit_rate_14d": None,
        }

    reconciled["abs_error"] = (
        reconciled["expected_return_pct"].astype(float)
        - reconciled["actual_return_pct"].astype(float)
    ).abs()

    def _metrics(frame: pd.DataFrame) -> tuple[float | None, float | None]:
        if frame.empty:
            return None, None
        mae = float(frame["abs_error"].mean())
        hits = frame["direction_correct"]
        hit_rate = float(hits.mean()) if hits.notna().any() else None
        return mae, hit_rate

    mae_all, hit_all = _metrics(reconciled)
    recent = reconciled.tail(max(1, window))
    mae_14d, hit_14d = _metrics(recent)

    walk_forward_hit = None
    eval_count = None
    try:
        from trade_integrations.dataflows.index_research.backtest_runner import load_backtest_report

        backtest = load_backtest_report("NIFTY") or {}
        metrics = backtest.get("metrics") or {}
        walk_forward_hit = metrics.get("direction_hit_rate_walk_forward") or metrics.get(
            "direction_hit_rate"
        )
        eval_count = backtest.get("eval_count")
    except Exception:
        walk_forward_hit = None
        eval_count = None

    return {
        "sample_count": int(len(reconciled)),
        "mae_pct": mae_all,
        "mae_14d_pct": mae_14d,
        "direction_hit_rate": walk_forward_hit if walk_forward_hit is not None else hit_all,
        "direction_hit_rate_walk_forward": walk_forward_hit,
        "direction_hit_rate_ledger": hit_all,
        "direction_hit_rate_14d": hit_14d,
        "eval_count": eval_count,
        "window_days": window,
    }
