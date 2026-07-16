"""Forecast logging and reconciliation for index research."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.models import PredictionRecord
from trade_integrations.dataflows.index_research.sources.history_loader import (
    NIFTY_SYMBOL,
    load_nifty_history,
)

_LEDGER_SUBDIR = "_data/index_predictions"
_LEDGER_FILENAME = "ledger.parquet"
_NEUTRAL_RETURN_THRESHOLD_PCT = 0.15


def get_ledger_path() -> Path:
    """Return path to the shared prediction ledger parquet file."""
    return get_hub_dir() / _LEDGER_SUBDIR / _LEDGER_FILENAME


def _write_parquet(df: pd.DataFrame, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path, index=False)
    except ImportError:
        df.to_csv(path.with_suffix(".csv"), index=False)


def _read_parquet(path) -> pd.DataFrame:
    if path.is_file():
        try:
            return pd.read_parquet(path)
        except ImportError:
            csv_path = path.with_suffix(".csv")
            if csv_path.is_file():
                return pd.read_csv(csv_path)
    csv_path = path.with_suffix(".csv")
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
    updated = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
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
    updated = 0

    for idx, row in ledger.iterrows():
        if pd.notna(row.get("actual_return_pct")):
            continue

        predicted_at = row["predicted_at"]
        if not isinstance(predicted_at, datetime):
            predicted_at = datetime.fromisoformat(str(predicted_at))
        pred_date = predicted_at.date() if hasattr(predicted_at, "date") else predicted_at
        horizon_days = int(row["horizon_days"])
        maturity = pred_date + timedelta(days=horizon_days)
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
    return updated


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

    return {
        "sample_count": int(len(reconciled)),
        "mae_pct": mae_all,
        "mae_14d_pct": mae_14d,
        "direction_hit_rate": hit_all,
        "direction_hit_rate_14d": hit_14d,
        "window_days": window,
    }
