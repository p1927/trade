"""Parquet-backed time-series storage for shared index factors."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.hub_storage.parquet_io import concat_dataframes, concat_frames

from trade_integrations.context.hub import get_hub_dir

_FACTOR_SUBDIR = "_data/index_factors/daily"
_MODEL_SUBDIR = "_data/index_factors/model"
_MODEL_FILENAME = "latest.json"


def get_factor_data_dir() -> Path:
    """Return the directory for daily factor parquet files."""
    return get_hub_dir() / _FACTOR_SUBDIR


def get_model_artifact_path() -> Path:
    """Return path to the persisted hybrid predictor model artifact."""
    return get_hub_dir() / _MODEL_SUBDIR / _MODEL_FILENAME


def save_model_artifact(artifact: dict[str, Any]) -> None:
    """Persist trained model coefficients and metadata as JSON."""
    path = get_model_artifact_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")


def load_model_artifact() -> dict[str, Any] | None:
    """Load the latest model artifact, or ``None`` if missing."""
    path = get_model_artifact_path()
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _daily_path(day: str) -> Path:
    return get_factor_data_dir() / f"{day}.parquet"


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    csv_path = path.with_suffix(".csv")
    try:
        df.to_parquet(path, index=False)
    except ImportError:
        df.to_csv(csv_path, index=False)
        return
    # Mirror CSV so runtimes without pyarrow (e.g. agent serve on system Python) still see backfills.
    df.to_csv(csv_path, index=False)


def _read_parquet(path: Path) -> pd.DataFrame:
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


def save_daily_factors(day: str, rows: list[dict]) -> None:
    """Persist factor rows for a single calendar day."""
    out_dir = get_factor_data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    enriched = [{**row, "date": day} for row in rows]
    df = pd.DataFrame(enriched)
    _write_parquet(df, _daily_path(day))


def upsert_daily_factors(day: str, rows: list[dict]) -> None:
    """Merge factor rows for a day, replacing rows with the same ``factor`` key."""
    if not rows:
        return
    existing = _read_parquet(_daily_path(day))
    new_factors = {str(row["factor"]) for row in rows if row.get("factor")}
    if not existing.empty and "factor" in existing.columns:
        kept = existing[~existing["factor"].astype(str).isin(new_factors)]
        merged = concat_dataframes(kept, pd.DataFrame([{**row, "date": day} for row in rows]))
    else:
        merged = pd.DataFrame([{**row, "date": day} for row in rows])
    out_dir = get_factor_data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_parquet(merged, _daily_path(day))


_LIGHT_ENRICHMENT_REQUIRED = frozenset({"repo_rate", "fii_net_5d", "dii_net_5d"})
_FULL_ENRICHMENT_REQUIRED = _LIGHT_ENRICHMENT_REQUIRED | frozenset(
    {"nifty_pe", "institutional_net_5d", "nifty_pcr"}
)


def load_day_factor_keys(day: str) -> set[str]:
    """Return factor keys already stored for a calendar day."""
    existing = _read_parquet(_daily_path(day))
    if existing.empty or "factor" not in existing.columns:
        return set()
    return {str(key) for key in existing["factor"].astype(str)}


def day_enrichment_complete(day: str, *, light_mode: bool = False) -> bool:
    """Return whether a day already has the required enrichment factor keys."""
    required = _LIGHT_ENRICHMENT_REQUIRED if light_mode else _FULL_ENRICHMENT_REQUIRED
    return required.issubset(load_day_factor_keys(day))


_MAX_ROLLING_LOOKBACK = 7


def select_enrichment_candidate_days(
    trading_dates: list[str],
    *,
    light_mode: bool = False,
    rolling_only: bool = False,
    max_lookback: int = _MAX_ROLLING_LOOKBACK,
) -> list[str]:
    """Return calendar days that may need enrichment writes.

    When *rolling_only* is set (scheduled light path), only the last
    *max_lookback* trading sessions are candidates — today plus rolling windows.
    """
    if not trading_dates:
        return []
    if rolling_only:
        candidates = trading_dates[-max(1, min(max_lookback, len(trading_dates))):]
        return filter_days_needing_enrichment(candidates, light_mode=light_mode)
    return filter_days_needing_enrichment(trading_dates, light_mode=light_mode)


def filter_days_needing_enrichment(trading_dates: list[str], *, light_mode: bool = False) -> list[str]:
    """Return trading dates in *trading_dates* missing required enrichment factors."""
    return [day for day in trading_dates if not day_enrichment_complete(day, light_mode=light_mode)]


def load_factor_history(start: str, end: str) -> pd.DataFrame:
    """Load factor rows for an inclusive date range (YYYY-MM-DD)."""
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    if end_d < start_d:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    current = start_d
    while current <= end_d:
        day = current.isoformat()
        path = _daily_path(day)
        if path.is_file() or path.with_suffix(".csv").is_file():
            frames.append(_read_parquet(path))
        current += timedelta(days=1)

    if not frames:
        return pd.DataFrame()
    return concat_frames(frames)
