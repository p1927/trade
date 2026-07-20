"""Shared parquet read/write with CSV mirror fallback."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def read_dataframe(path: Path) -> pd.DataFrame:
    parquet_exc: Exception | None = None
    if path.is_file():
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            parquet_exc = exc
            logger.warning("Failed to read parquet %s: %s", path, exc)
    csv_path = path.with_suffix(".csv")
    if csv_path.is_file():
        try:
            return pd.read_csv(csv_path)
        except Exception as exc:
            logger.warning("Failed to read CSV fallback %s: %s", csv_path, exc)
            if parquet_exc is not None:
                raise parquet_exc from exc
            return pd.DataFrame()
    if parquet_exc is not None:
        raise parquet_exc
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
