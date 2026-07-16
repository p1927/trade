"""Parsers for NSE monthly SEBI institutional flow tables (equity + debt, ₹ Cr)."""

from __future__ import annotations

import re
from io import StringIO
from typing import Any

import pandas as pd

from trade_integrations.nse_browser.parsers.fii_dii import _parse_money, _parse_month_label


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    mapping = {}
    for col in frame.columns:
        key = re.sub(r"[^a-z0-9]+", "_", str(col).lower()).strip("_")
        mapping[col] = key
    return frame.rename(columns=mapping)


def _pick_col(columns: list[str], *needles: str) -> str | None:
    for col in columns:
        if all(n in col for n in needles):
            return col
    return None


def parse_sebi_monthly_equity_debt_csv(text: str, *, source: str) -> pd.DataFrame:
    """
    Parse monthly SEBI table with equity and debt gross buy/sell/net (₹ Cr).

    Expects: Date, equity purchase/sales/net, debt purchase/sales/net.
    """
    if not text or len(text.strip()) < 20:
        return pd.DataFrame()
    try:
        raw = pd.read_csv(StringIO(text), encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    frame = _normalize_columns(raw)
    cols = list(frame.columns)
    date_col = next((c for c in cols if "date" in c), cols[0])
    equity_buy = _pick_col(cols, "equity", "purchase") or _pick_col(cols, "equity", "buy")
    equity_sell = _pick_col(cols, "equity", "sales") or _pick_col(cols, "equity", "sell")
    equity_net = _pick_col(cols, "equity", "net")
    debt_buy = _pick_col(cols, "debt", "purchase") or _pick_col(cols, "debt", "buy")
    debt_sell = _pick_col(cols, "debt", "sales") or _pick_col(cols, "debt", "sell")
    debt_net = _pick_col(cols, "debt", "net")

    if not equity_net and len(cols) >= 7:
        equity_buy, equity_sell, equity_net = cols[1], cols[2], cols[3]
        debt_buy, debt_sell, debt_net = cols[4], cols[5], cols[6]

    rows: list[dict[str, Any]] = []
    for _, item in frame.iterrows():
        day = _parse_month_label(item.get(date_col))
        if not day:
            continue
        row: dict[str, Any] = {
            "date": day,
            "source": source,
            "variant": "monthly",
            "granularity": "monthly",
        }
        mapping = (
            (equity_buy, "equity_buy"),
            (equity_sell, "equity_sell"),
            (equity_net, "equity_net"),
            (debt_buy, "debt_buy"),
            (debt_sell, "debt_sell"),
            (debt_net, "debt_net"),
        )
        for col, dest in mapping:
            if col is None:
                continue
            val = _parse_money(item.get(col))
            if val is not None:
                row[dest] = val
        if "equity_net" in row or "debt_net" in row:
            rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")


def parse_mf_sebi_monthly_csv(text: str) -> pd.DataFrame:
    """Mutual fund monthly SEBI flows (equity + debt)."""
    return parse_sebi_monthly_equity_debt_csv(text, source="nse_mf_sebi_monthly")


def parse_fii_sebi_monthly_csv(text: str) -> pd.DataFrame:
    """FII/FPI monthly SEBI flows (equity + debt)."""
    return parse_sebi_monthly_equity_debt_csv(text, source="nse_fii_sebi_monthly")
