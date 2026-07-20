"""Forecast logging and reconciliation for options research recommendations."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.hub_storage.parquet_io import concat_dataframes

from trade_integrations.context.hub import get_hub_dir

_LEDGER_SUBDIR = "_data/options_predictions"
_LEDGER_FILENAME = "ledger.parquet"
_NEUTRAL_MOVE_THRESHOLD_PCT = 0.25


@dataclass
class OptionsPredictionRecord:
    """Logged options recommendation for later reconciliation at expiry."""

    underlying: str
    predicted_at: datetime
    expiry_date: str
    spot_at_prediction: float
    prediction_view: str
    expected_move_pct: float
    strategy_name: str
    strategy_score: float
    actual_return_pct: float | None = None
    move_within_expected: bool | None = None
    direction_correct: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def get_ledger_path() -> Path:
    return get_hub_dir() / _LEDGER_SUBDIR / _LEDGER_FILENAME


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path, index=False)
    except ImportError:
        df.to_csv(path.with_suffix(".csv"), index=False)


def _read_parquet(path: Path) -> pd.DataFrame:
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


def _record_to_row(record: OptionsPredictionRecord) -> dict[str, Any]:
    return {
        "underlying": record.underlying.upper(),
        "predicted_at": record.predicted_at.isoformat(),
        "expiry_date": record.expiry_date,
        "spot_at_prediction": float(record.spot_at_prediction),
        "prediction_view": record.prediction_view,
        "expected_move_pct": float(record.expected_move_pct),
        "strategy_name": record.strategy_name,
        "strategy_score": float(record.strategy_score),
        "actual_return_pct": record.actual_return_pct,
        "move_within_expected": record.move_within_expected,
        "direction_correct": record.direction_correct,
        "metadata_json": json.dumps(record.metadata or {}, default=str),
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


def load_ledger() -> pd.DataFrame:
    return _read_parquet(get_ledger_path())


def save_ledger(df: pd.DataFrame) -> None:
    _write_parquet(df, get_ledger_path())


def append_options_prediction(record: OptionsPredictionRecord) -> None:
    """Append a recommended-strategy forecast row to the options ledger."""
    row = _record_to_row(record)
    existing = load_ledger()
    updated = concat_dataframes(existing, pd.DataFrame([row]))
    save_ledger(updated)


def _parse_date(value: str | date) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _direction_correct(view: str, actual_return_pct: float) -> bool:
    view_key = view.strip().lower()
    if abs(actual_return_pct) < _NEUTRAL_MOVE_THRESHOLD_PCT:
        return "neutral" in view_key or "range" in view_key
    if actual_return_pct > 0:
        return any(k in view_key for k in ("bull", "long", "debit", "call"))
    return any(k in view_key for k in ("bear", "short", "put"))


def _fetch_close_on(target: date, underlying: str) -> float | None:
    """Resolve underlying close on or before target date."""
    try:
        from trade_integrations.monitor.live_quotes import fetch_underlying_ltp

        ltp = fetch_underlying_ltp(underlying)
        if ltp is not None and target >= date.today():
            return float(ltp)
    except Exception:
        pass

    try:
        import yfinance as yf

        sym = underlying.upper()
        yf_symbol = "^NSEI" if sym in {"NIFTY", "NIFTY50"} else f"{sym}.NS"
        start = target - timedelta(days=14)
        end = target + timedelta(days=3)
        hist = yf.Ticker(yf_symbol).history(start=start.isoformat(), end=end.isoformat())
        if hist.empty:
            return None
        hist = hist.reset_index()
        date_col = "Date" if "Date" in hist.columns else hist.columns[0]
        close_col = "Close" if "Close" in hist.columns else "close"
        frame = pd.DataFrame(
            {
                "day": pd.to_datetime(hist[date_col]).dt.date,
                "close": hist[close_col].astype(float),
            }
        )
        eligible = frame[frame["day"] <= target]
        if eligible.empty:
            return None
        return float(eligible.iloc[-1]["close"])
    except Exception:
        return None


def reconcile_options_predictions(*, as_of: datetime | None = None) -> int:
    """Fill actual returns for matured option expiries."""
    now = as_of or datetime.now(timezone.utc)
    today = now.date()
    ledger = load_ledger()
    if ledger.empty:
        return 0

    updated = 0
    for idx, row in ledger.iterrows():
        if pd.notna(row.get("actual_return_pct")):
            continue

        expiry = _parse_date(str(row.get("expiry_date") or ""))
        if expiry is None or expiry > today:
            continue

        spot = float(row["spot_at_prediction"])
        underlying = str(row["underlying"])
        actual_close = _fetch_close_on(expiry, underlying)
        if actual_close is None or spot <= 0:
            continue

        actual_return_pct = (actual_close - spot) / spot * 100.0
        expected_move = abs(float(row.get("expected_move_pct") or 0.0))
        view = str(row.get("prediction_view") or "")
        ledger.at[idx, "actual_return_pct"] = actual_return_pct
        ledger.at[idx, "move_within_expected"] = (
            abs(actual_return_pct) <= expected_move if expected_move > 0 else None
        )
        ledger.at[idx, "direction_correct"] = _direction_correct(view, actual_return_pct)
        updated += 1

    if updated:
        save_ledger(ledger)
    return updated


def compute_options_accuracy_metrics(*, window: int = 20) -> dict[str, Any]:
    """Rolling calibration stats from reconciled options predictions."""
    ledger = load_ledger()
    if ledger.empty or "actual_return_pct" not in ledger.columns:
        return {
            "sample_count": 0,
            "mae_pct": None,
            "move_hit_rate": None,
            "direction_hit_rate": None,
            "direction_hit_rate_window": None,
            "window": window,
        }

    reconciled = ledger[ledger["actual_return_pct"].notna()].copy()
    if reconciled.empty:
        return {
            "sample_count": 0,
            "mae_pct": None,
            "move_hit_rate": None,
            "direction_hit_rate": None,
            "direction_hit_rate_window": None,
            "window": window,
        }

    reconciled["abs_error"] = (
        reconciled["expected_move_pct"].astype(float).abs()
        - reconciled["actual_return_pct"].astype(float).abs()
    ).abs()

    move_hits = reconciled["move_within_expected"]
    direction_hits = reconciled["direction_correct"]
    recent = reconciled.tail(max(1, window))

    return {
        "sample_count": int(len(reconciled)),
        "mae_pct": float(reconciled["abs_error"].mean()),
        "move_hit_rate": float(move_hits.mean()) if move_hits.notna().any() else None,
        "direction_hit_rate": float(direction_hits.mean()) if direction_hits.notna().any() else None,
        "direction_hit_rate_window": (
            float(recent["direction_correct"].mean())
            if recent["direction_correct"].notna().any()
            else None
        ),
        "window": window,
    }


def calibration_confidence_adjustment() -> float:
    """Small score boost/penalty from ledger calibration (stub for ranker)."""
    metrics = compute_options_accuracy_metrics()
    if metrics["sample_count"] < 5:
        return 0.0
    hit = metrics.get("direction_hit_rate_window") or metrics.get("direction_hit_rate")
    if hit is None:
        return 0.0
    if hit >= 0.6:
        return 0.03
    if hit <= 0.4:
        return -0.03
    return 0.0
