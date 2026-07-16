"""Parquet-backed time-series storage for shared index factors."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

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
    try:
        df.to_parquet(path, index=False)
    except ImportError:
        csv_path = path.with_suffix(".csv")
        df.to_csv(csv_path, index=False)


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


def save_daily_factors(day: str, rows: list[dict]) -> None:
    """Persist factor rows for a single calendar day."""
    out_dir = get_factor_data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    enriched = [{**row, "date": day} for row in rows]
    df = pd.DataFrame(enriched)
    _write_parquet(df, _daily_path(day))


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
    return pd.concat(frames, ignore_index=True)
