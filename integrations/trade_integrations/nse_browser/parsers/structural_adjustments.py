"""Corporate-action and F&O settlement adjustments for historic NSE datasets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from trade_integrations.hub_storage.parquet_io import combine_first_numeric
from trade_integrations.dataflows.index_research.calendar_features import last_thursday_of_month


@dataclass(frozen=True)
class SymbolSuccession:
    """Map a predecessor Nifty 50 ticker to its successor after a corporate action."""

    from_symbol: str
    to_symbol: str
    effective_date: str  # ISO YYYY-MM-DD — first session under successor-only semantics
    kind: str  # merger | rename | demerger


# Predecessor → successor (India Nifty 50 index history).
NIFTY50_SYMBOL_SUCCESSIONS: tuple[SymbolSuccession, ...] = (
    SymbolSuccession("HDFC", "HDFCBANK", "2023-07-13", "merger"),
    SymbolSuccession("INFRATEL", "INDUSTOWER", "2020-09-01", "rename"),
    SymbolSuccession("LTI", "LTIM", "2022-11-18", "merger"),
    SymbolSuccession("MCDOWELL-N", "UNITDSPR", "2020-10-01", "rename"),
    SymbolSuccession("IBULHSGFIN", "SAMMAANCAP", "2024-04-01", "rename"),
)


def resolve_successor_symbol(symbol: str, as_of: str) -> str:
    """Return successor ticker when ``symbol`` is obsolete on/after ``as_of``."""
    sym = str(symbol or "").strip().upper()
    day = str(as_of)[:10]
    for rule in NIFTY50_SYMBOL_SUCCESSIONS:
        if sym == rule.from_symbol and day >= rule.effective_date:
            return rule.to_symbol
    return sym


def is_fo_monthly_expiry_day(day: str | date) -> bool:
    if isinstance(day, str):
        parsed = date.fromisoformat(day[:10])
    else:
        parsed = day
    return parsed == last_thursday_of_month(parsed.year, parsed.month)


def enrich_adjusted_constituent_prices(frame: pd.DataFrame) -> pd.DataFrame:
    """
    Prefer split/bonus-adjusted prices for return math.

    Sets ``close`` to ``adj_close`` when present; preserves ``close_raw``.
    Recomputes ``daily_return_pct`` from adjusted closes when possible.
    """
    if frame.empty or "close" not in frame.columns:
        return frame

    out = frame.copy()
    out["close_raw"] = pd.to_numeric(out["close"], errors="coerce")
    if "adj_close" in out.columns:
        adj = pd.to_numeric(out["adj_close"], errors="coerce")
        out["close"] = combine_first_numeric(adj, out["close_raw"])
        out["price_basis"] = np.where(adj.notna(), "adj_close", "close")
    else:
        out["price_basis"] = "close"

    if "symbol" in out.columns and "date" in out.columns:
        out = out.sort_values(["symbol", "date"]).reset_index(drop=True)
        grouped = out.groupby("symbol", sort=False)["close"]
        out["daily_return_pct"] = grouped.pct_change() * 100.0

    out["symbol_raw"] = out["symbol"] if "symbol" in out.columns else pd.NA
    if "symbol" in out.columns and "date" in out.columns:
        out["symbol"] = [
            resolve_successor_symbol(sym, day)
            for sym, day in zip(out["symbol"].astype(str), out["date"].astype(str), strict=False)
        ]
        out = (
            out.sort_values(["date", "symbol"])
            .drop_duplicates(["date", "symbol"], keep="last")
            .reset_index(drop=True)
        )
    return out


def apply_symbol_succession_to_weights_wide(wide: pd.DataFrame) -> pd.DataFrame:
    """
    Roll predecessor weight columns into successors from effective dates onward.

    Example: HDFC weight after 2023-07-13 merges into HDFCBANK; HDFC column zeroed.
    """
    if wide.empty or "date" not in wide.columns:
        return wide

    out = wide.copy()
    out["date"] = out["date"].astype(str).str[:10]
    symbol_cols = [c for c in out.columns if c not in {"date", "source", "source_file"}]

    for rule in NIFTY50_SYMBOL_SUCCESSIONS:
        predecessor = rule.from_symbol.upper()
        successor = rule.to_symbol.upper()
        if predecessor not in out.columns:
            continue
        if successor not in out.columns:
            out[successor] = np.nan
        mask = out["date"] >= rule.effective_date
        pred_vals = pd.to_numeric(out.loc[mask, predecessor], errors="coerce").fillna(0.0)
        succ_vals = pd.to_numeric(out.loc[mask, successor], errors="coerce").fillna(0.0)
        out.loc[mask, successor] = succ_vals + pred_vals
        out.loc[mask, predecessor] = 0.0

    # Drop columns that are permanently zero after succession handling.
    for col in symbol_cols:
        if col in out.columns and pd.to_numeric(out[col], errors="coerce").fillna(0.0).sum() == 0:
            out = out.drop(columns=[col])
    return out.reset_index(drop=True)


def rebuild_weights_long(wide: pd.DataFrame, *, source: str) -> pd.DataFrame:
    """Explode wide monthly weights to long membership rows."""
    if wide.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for _, row in wide.iterrows():
        day = str(row["date"])[:10]
        for col in wide.columns:
            if col in {"date", "source", "source_file"}:
                continue
            val = row.get(col)
            if pd.notna(val) and float(val) > 0:
                symbol = resolve_successor_symbol(str(col).upper(), day)
                rows.append(
                    {
                        "date": day,
                        "symbol": symbol,
                        "weight": float(val),
                        "in_index": 1.0,
                        "source": source,
                    }
                )
    if not rows:
        return pd.DataFrame()
    long_panel = pd.DataFrame(rows)
    return (
        long_panel.sort_values(["date", "symbol"])
        .drop_duplicates(["date", "symbol"], keep="last")
        .reset_index(drop=True)
    )


def adjust_institutional_flow_expiry_settlement(frame: pd.DataFrame) -> pd.DataFrame:
    """
    De-spike FII/DII on monthly F&O expiry (last Thursday).

    Preserves raw values in ``*_raw``; writes settlement-adjusted series to
    ``fii_net`` / ``dii_net`` and ``*_settlement_adj`` (same values).
    """
    if frame.empty or "date" not in frame.columns:
        return frame

    out = frame.copy()
    out["date"] = out["date"].astype(str).str[:10]
    out = out.sort_values("date").reset_index(drop=True)
    out["is_fo_monthly_expiry"] = out["date"].map(is_fo_monthly_expiry_day).astype(bool)

    for col in ("fii_net", "dii_net"):
        if col not in out.columns:
            continue
        raw_col = f"{col}_raw"
        col_vals = pd.to_numeric(out[col], errors="coerce")
        if raw_col in out.columns:
            raw = pd.to_numeric(out[raw_col], errors="coerce").combine_first(col_vals)
        else:
            raw = col_vals
        out[raw_col] = raw
        # Prior-session median of last 5 non-expiry observations (settlement noise filter).
        baseline = raw.where(~out["is_fo_monthly_expiry"]).rolling(5, min_periods=2).median().shift(1)
        adjusted = raw.copy()
        adjusted.loc[out["is_fo_monthly_expiry"]] = baseline.loc[out["is_fo_monthly_expiry"]]
        adjusted = combine_first_numeric(adjusted, raw)
        out[f"{col}_settlement_adj"] = adjusted
        out[col] = adjusted

    return out
