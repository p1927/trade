"""Parse Nifty 50 stock F&O bhavcopy CSV into daily PCR / positioning proxies."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

_DERIV_COLUMNS: tuple[str, ...] = (
    "nifty_pcr",
    "fii_fut_long_short_ratio",
    "fii_idx_fut_long",
    "fii_idx_fut_short",
    "fii_idx_put_oi",
    "fii_idx_call_oi",
)


def _parse_bhavcopy_date(raw: Any) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:11], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _aggregate_fo_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    if chunk.empty or "TIMESTAMP" not in chunk.columns:
        return pd.DataFrame()

    work = chunk.copy()
    work["date"] = work["TIMESTAMP"].map(_parse_bhavcopy_date)
    work = work[work["date"].notna()]
    if work.empty:
        return pd.DataFrame()

    work["open_int"] = pd.to_numeric(work.get("OPEN_INT"), errors="coerce").fillna(0.0)
    work["chg_in_oi"] = pd.to_numeric(work.get("CHG_IN_OI"), errors="coerce").fillna(0.0)
    work["instrument"] = work.get("INSTRUMENT", pd.Series(dtype=str)).astype(str).str.upper()
    work["option_typ"] = work.get("OPTION_TYP", pd.Series(dtype=str)).astype(str).str.upper()

    rows: list[dict[str, Any]] = []
    for day, group in work.groupby("date", sort=True):
        opts = group[group["instrument"] == "OPTSTK"]
        calls = opts[opts["option_typ"] == "CE"]
        puts = opts[opts["option_typ"] == "PE"]
        call_oi = float(calls["open_int"].sum())
        put_oi = float(puts["open_int"].sum())

        fut = group[group["instrument"] == "FUTSTK"]
        long_build = float(fut.loc[fut["chg_in_oi"] > 0, "chg_in_oi"].sum())
        short_build = float(fut.loc[fut["chg_in_oi"] < 0, "chg_in_oi"].abs().sum())
        fut_oi = float(fut["open_int"].sum())

        row: dict[str, Any] = {"date": str(day)}
        if call_oi > 0 and put_oi >= 0:
            row["fii_idx_call_oi"] = call_oi
            row["fii_idx_put_oi"] = put_oi
            row["nifty_pcr"] = put_oi / call_oi
        if short_build > 0 and long_build > 0:
            row["fii_idx_fut_long"] = long_build
            row["fii_idx_fut_short"] = short_build
            row["fii_fut_long_short_ratio"] = long_build / short_build
        elif fut_oi > 0:
            row["fii_idx_fut_long"] = fut_oi
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def parse_nifty50_fo_bhavcopy_csv(
    path: Path | str,
    *,
    source: str = "historic_data_nifty50_fo",
    chunksize: int = 250_000,
) -> pd.DataFrame:
    """
    Aggregate Nifty 50 stock F&O bhavcopy rows to daily PCR / F&O proxies.

    Uses summed OPTSTK put/call open interest for ``nifty_pcr`` and FUTSTK OI
    change direction for ``fii_fut_long_short_ratio`` when participant OI is absent.
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        return pd.DataFrame()

    parts: list[pd.DataFrame] = []
    for chunk in pd.read_csv(csv_path, chunksize=chunksize):
        part = _aggregate_fo_chunk(chunk)
        if not part.empty:
            parts.append(part)

    if not parts:
        return pd.DataFrame()

    merged = pd.concat(parts, ignore_index=True)
    numeric_cols = [c for c in merged.columns if c != "date"]
    grouped = merged.groupby("date", as_index=False)[numeric_cols].sum(min_count=1)

    if {"fii_idx_put_oi", "fii_idx_call_oi"}.issubset(grouped.columns):
        call_oi = grouped["fii_idx_call_oi"].replace(0, pd.NA)
        grouped["nifty_pcr"] = grouped["nifty_pcr"].combine_first(
            grouped["fii_idx_put_oi"] / call_oi
        )
    if {"fii_idx_fut_long", "fii_idx_fut_short"}.issubset(grouped.columns):
        short = grouped["fii_idx_fut_short"].replace(0, pd.NA)
        grouped["fii_fut_long_short_ratio"] = grouped["fii_fut_long_short_ratio"].combine_first(
            grouped["fii_idx_fut_long"] / short
        )

    grouped["source"] = source
    grouped["granularity"] = "daily"
    grouped["variant"] = "derivatives"
    return grouped.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
