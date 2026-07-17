"""Shared parquet read/write with CSV mirror fallback."""

from __future__ import annotations

import uuid
from pathlib import Path

import pandas as pd


def read_dataframe(path: Path) -> pd.DataFrame:
    if path.is_file():
        try:
            return pd.read_parquet(path)
        except Exception:
            pass
    csv_path = path.with_suffix(".csv")
    if csv_path.is_file():
        try:
            return pd.read_csv(csv_path)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def write_dataframe(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = path.with_suffix(".csv")
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        df.to_parquet(tmp, index=False)
        tmp.replace(path)
    except ImportError:
        tmp.unlink(missing_ok=True)
        df.to_csv(csv_path, index=False)
        return
    finally:
        if tmp.is_file():
            tmp.unlink(missing_ok=True)
    df.to_csv(csv_path, index=False)
