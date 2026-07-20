"""Parsers for NSE historical market archive downloads."""

from __future__ import annotations

import re
from io import StringIO
from typing import Any

import pandas as pd

from trade_integrations.hub_storage.parquet_io import concat_dataframes


def _norm_cols(frame: pd.DataFrame) -> pd.DataFrame:
    mapping = {}
    for col in frame.columns:
        key = re.sub(r"[^a-z0-9]+", "_", str(col).lower()).strip("_")
        mapping[col] = key
    return frame.rename(columns=mapping)


def parse_archive_csv(text: str, *, dataset: str) -> pd.DataFrame:
    """Parse generic NSE archive CSV into normalized frame."""
    if not text or len(text.strip()) < 20:
        return pd.DataFrame()
    try:
        raw = pd.read_csv(StringIO(text))
    except Exception:
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()
    frame = _norm_cols(raw)
    frame["dataset"] = dataset
    frame["source"] = "nse_browser_archives"
    date_col = next((c for c in frame.columns if "date" in c or "traded" in c), None)
    if date_col:
        frame["date"] = pd.to_datetime(frame[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
    return frame


def parse_pe_pb_csv(text: str) -> pd.DataFrame:
    frame = parse_archive_csv(text, dataset="pe_pb")
    pe_col = next((c for c in frame.columns if c in {"p_e", "pe", "pe_ratio"} or "p_e" in c), None)
    pb_col = next((c for c in frame.columns if "p_b" in c or c == "pb"), None)
    if pe_col and pe_col != "pe_ratio":
        frame["pe_ratio"] = pd.to_numeric(frame[pe_col], errors="coerce")
    if pb_col and pb_col != "pb_ratio":
        frame["pb_ratio"] = pd.to_numeric(frame[pb_col], errors="coerce")
    return frame


def parse_bulk_deals_csv(text: str) -> pd.DataFrame:
    return parse_archive_csv(text, dataset="bulk_deals")


def parse_delivery_csv(text: str) -> pd.DataFrame:
    return parse_archive_csv(text, dataset="delivery")


def merge_archive_frames(existing: pd.DataFrame, incoming: pd.DataFrame, *, key_cols: list[str]) -> pd.DataFrame:
    if incoming.empty:
        return existing
    if existing.empty:
        return incoming
    cols = [c for c in key_cols if c in incoming.columns]
    if not cols:
        return concat_dataframes(existing, incoming).drop_duplicates(keep="last")
    keep = existing[~existing[cols].astype(str).apply(tuple, axis=1).isin(
        incoming[cols].astype(str).apply(tuple, axis=1)
    )]
    merged = concat_dataframes(keep, incoming)
    return merged.drop_duplicates(subset=cols, keep="last")
