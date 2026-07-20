"""Parse GitHub datasets CSV files into normalized macro frames."""

from __future__ import annotations

from typing import Any

import pandas as pd

from trade_integrations.hub_storage.date_parse import format_date_series, parse_date_series

from .config import SOURCE_NAME

# Countries stored as wide FX columns (USD per unit of foreign currency where noted).
_FX_COUNTRIES: tuple[tuple[str, str], ...] = (
    ("India", "usd_inr"),
    ("Euro", "eur_usd"),
    ("Japan", "jpy_usd"),
    ("China", "cny_usd"),
    ("United Kingdom", "gbp_usd"),
)


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]
    return out


def parse_us_10y_monthly(path: str) -> pd.DataFrame:
    frame = _normalize_columns(pd.read_csv(path))
    frame = frame.rename(columns={"rate": "us_10y"})
    frame["date"] = parse_date_series(frame["date"])
    frame = frame.dropna(subset=["date", "us_10y"])
    frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
    frame["us_10y"] = pd.to_numeric(frame["us_10y"], errors="coerce")
    frame["source"] = SOURCE_NAME
    return frame[["date", "us_10y", "source"]].dropna(subset=["us_10y"]).reset_index(drop=True)


def parse_gold_monthly(path: str) -> pd.DataFrame:
    frame = _normalize_columns(pd.read_csv(path))
    frame = frame.rename(columns={"price": "gold"})
    raw_dates = frame["date"].astype(str).str.strip()
    parsed = parse_date_series(raw_dates)
    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = parse_date_series(raw_dates.loc[missing] + "-01")
    frame["date"] = parsed
    frame = frame.dropna(subset=["date", "gold"])
    frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
    frame["gold"] = pd.to_numeric(frame["gold"], errors="coerce")
    frame["source"] = SOURCE_NAME
    return frame[["date", "gold", "source"]].dropna(subset=["gold"]).reset_index(drop=True)


def parse_vix_daily(path: str) -> pd.DataFrame:
    frame = _normalize_columns(pd.read_csv(path))
    frame["date"] = format_date_series(frame["date"])
    frame["vix"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["vix_open"] = pd.to_numeric(frame.get("open"), errors="coerce")
    frame["vix_high"] = pd.to_numeric(frame.get("high"), errors="coerce")
    frame["vix_low"] = pd.to_numeric(frame.get("low"), errors="coerce")
    frame["source"] = SOURCE_NAME
    return (
        frame[["date", "vix", "vix_open", "vix_high", "vix_low", "source"]]
        .dropna(subset=["vix"])
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def parse_oil_daily(path: str, *, column: str) -> pd.DataFrame:
    frame = _normalize_columns(pd.read_csv(path))
    frame = frame.rename(columns={"price": column})
    frame["date"] = format_date_series(frame["date"])
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["source"] = SOURCE_NAME
    return (
        frame[["date", column, "source"]]
        .dropna(subset=[column])
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def parse_us_cpi(path: str) -> pd.DataFrame:
    frame = _normalize_columns(pd.read_csv(path))
    frame["date"] = format_date_series(frame["date"])
    frame["us_cpi_index"] = pd.to_numeric(frame.get("index"), errors="coerce")
    frame["us_cpi_inflation_pct"] = pd.to_numeric(frame.get("inflation"), errors="coerce")
    frame["source"] = SOURCE_NAME
    return (
        frame[["date", "us_cpi_index", "us_cpi_inflation_pct", "source"]]
        .dropna(subset=["us_cpi_index"])
        .reset_index(drop=True)
    )


def parse_us_gdp_quarter(path: str) -> pd.DataFrame:
    frame = _normalize_columns(pd.read_csv(path))
    frame["date"] = format_date_series(frame["date"])
    frame["us_gdp_level"] = pd.to_numeric(frame.get("level-current"), errors="coerce")
    frame["us_gdp_change_pct"] = pd.to_numeric(frame.get("change-current"), errors="coerce")
    frame["source"] = SOURCE_NAME
    return (
        frame[["date", "us_gdp_level", "us_gdp_change_pct", "source"]]
        .dropna(subset=["us_gdp_level"])
        .reset_index(drop=True)
    )


def parse_exchange_rates_daily(path: str) -> pd.DataFrame:
    frame = _normalize_columns(pd.read_csv(path))
    frame = frame.rename(columns={"exchange_rate": "rate"})
    frame["date"] = format_date_series(frame["date"])
    frame["rate"] = pd.to_numeric(frame["rate"], errors="coerce")
    frame["country"] = frame["country"].astype(str).str.strip()
    frame = frame.dropna(subset=["date", "rate", "country"])
    frame["source"] = SOURCE_NAME

    selected = frame[frame["country"].isin([c for c, _ in _FX_COUNTRIES])].copy()
    col_map = dict(_FX_COUNTRIES)
    selected["column"] = selected["country"].map(col_map)

    wide = selected.pivot_table(index="date", columns="column", values="rate", aggfunc="last").reset_index()
    wide["source"] = SOURCE_NAME
    wide["date"] = wide["date"].astype(str).str[:10]
    return wide.sort_values("date").reset_index(drop=True)


def expand_to_daily(frame: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    """Forward-fill monthly (or sparse) observations onto a daily calendar."""
    if frame.empty:
        return frame
    work = frame.copy()
    work["date"] = parse_date_series(work["date"].astype(str))
    work = work.sort_values("date").drop_duplicates("date", keep="last")
    start = work["date"].min()
    end = work["date"].max()
    daily_index = pd.date_range(start, end, freq="D")
    daily = pd.DataFrame({"date": daily_index.strftime("%Y-%m-%d")})
    merge_cols = ["date"] + [c for c in value_cols if c in work.columns]
    if "source" in work.columns:
        merge_cols.append("source")
    expanded = daily.merge(work[merge_cols].assign(date=work["date"].dt.strftime("%Y-%m-%d")), on="date", how="left")
    for col in value_cols:
        if col in expanded.columns:
            expanded[col] = expanded[col].ffill()
    if "source" in expanded.columns:
        expanded["source"] = expanded["source"].ffill().fillna(SOURCE_NAME)
    return expanded.reset_index(drop=True)


def factor_series(frame: pd.DataFrame, factor: str) -> pd.Series:
    if frame.empty or factor not in frame.columns:
        return pd.Series(dtype=float)
    out = frame[["date", factor]].dropna(subset=[factor]).copy()
    out = out.drop_duplicates("date", keep="last")
    return pd.Series(out[factor].astype(float).values, index=out["date"].astype(str), name=factor)
