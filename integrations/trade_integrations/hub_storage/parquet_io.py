"""Shared parquet read/write with CSV mirror fallback."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def concat_dataframes(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """Concatenate capture frames without pandas empty/all-NA concat warnings."""
    if existing.empty:
        return incoming.copy()
    if incoming.empty:
        return existing.copy()
    columns = list(dict.fromkeys(list(existing.columns) + list(incoming.columns)))
    existing = existing.reindex(columns=columns)
    incoming = incoming.reindex(columns=columns)
    return pd.DataFrame(existing.to_dict("records") + incoming.to_dict("records"))


def append_dataframe(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """Readable alias for row-oriented frame append."""
    return concat_dataframes(existing, incoming)


def concat_frames(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Stack row-oriented frames without pandas empty/all-NA concat warnings."""
    merged = pd.DataFrame()
    for frame in frames:
        if frame is None or frame.empty:
            continue
        merged = concat_dataframes(merged, frame)
    return merged


def append_daily_rows(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    dedupe_keys: list[str] | None = None,
    sort_key: str | None = None,
) -> int:
    """Read daily parquet, append rows, dedupe, write. Returns incoming row count."""
    if not rows:
        return 0
    incoming = pd.DataFrame(rows)
    existing = read_dataframe(path)
    merged = upsert_by_keys(
        existing,
        incoming,
        dedupe_keys=list(dedupe_keys or []),
        sort_key=sort_key,
    )
    write_dataframe(merged, path)
    return len(incoming)


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


def upsert_by_keys(
    existing: pd.DataFrame,
    incoming: pd.DataFrame,
    *,
    dedupe_keys: list[str],
    sort_key: str | None = None,
) -> pd.DataFrame:
    """Merge frames keeping last row per dedupe key set."""
    if existing.empty:
        merged = incoming.copy()
    elif incoming.empty:
        merged = existing.copy()
    else:
        merged = concat_dataframes(existing, incoming)
    keys = [k for k in dedupe_keys if k in merged.columns]
    if not keys:
        return merged
    if sort_key and sort_key in merged.columns:
        merged = merged.sort_values(sort_key)
    return merged.drop_duplicates(keys, keep="last").reset_index(drop=True)
