"""Parse FII/DII cash tables from third-party web sources (Moneycontrol, Nifty Invest)."""

from __future__ import annotations

import re
from datetime import datetime
from io import StringIO
from typing import Any

import pandas as pd

from trade_integrations.hub_storage.date_parse import parse_date_scalar
from trade_integrations.nse_browser.parsers.fii_dii import _parse_money


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    mapping = {}
    for col in frame.columns:
        key = re.sub(r"[^a-z0-9]+", "_", str(col).lower()).strip("_")
        mapping[col] = key
    return frame.rename(columns=mapping)


def parse_moneycontrol_cash_html(html: str) -> pd.DataFrame:
    """
    Parse Moneycontrol FII/DII cash daily table (gross buy/sell/net in ₹ Cr).

    Handles legacy table rows like ``10-Jul-2026`` with six numeric columns.
    """
    if not html or len(html.strip()) < 100:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    # Legacy cash table: date token followed by six money columns
    row_re = re.compile(
        r"(\d{1,2}-[A-Za-z]{3}-\d{4})\D+"
        r"([\d,]+\.?\d*)\D+([\d,]+\.?\d*)\D+(-?[\d,]+\.?\d*)\D+"
        r"([\d,]+\.?\d*)\D+([\d,]+\.?\d*)\D+(-?[\d,]+\.?\d*)"
    )
    for match in row_re.finditer(html):
        day = parse_date_scalar(match.group(1))
        if not day:
            continue
        fii_buy = _parse_money(match.group(2))
        fii_sell = _parse_money(match.group(3))
        fii_net = _parse_money(match.group(4))
        dii_buy = _parse_money(match.group(5))
        dii_sell = _parse_money(match.group(6))
        dii_net = _parse_money(match.group(7))
        if fii_net is None and dii_net is None:
            continue
        row: dict[str, Any] = {
            "date": day,
            "source": "moneycontrol_cash",
            "variant": "cash",
            "granularity": "daily",
        }
        if fii_buy is not None:
            row["fii_buy"] = fii_buy
        if fii_sell is not None:
            row["fii_sell"] = fii_sell
        if fii_net is not None:
            row["fii_net"] = fii_net
        if dii_buy is not None:
            row["dii_buy"] = dii_buy
        if dii_sell is not None:
            row["dii_sell"] = dii_sell
        if dii_net is not None:
            row["dii_net"] = dii_net
        rows.append(row)

    if rows:
        return pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")

    # Fallback: pandas read_html on first matching table
    try:
        tables = pd.read_html(StringIO(html))
    except Exception:
        return pd.DataFrame()
    for raw in tables:
        if raw.empty or raw.shape[1] < 7:
            continue
        frame = _normalize_columns(raw)
        date_col = next((c for c in frame.columns if "date" in c), frame.columns[0])
        parsed: list[dict[str, Any]] = []
        cols = list(frame.columns)
        fii_buy, fii_sell, fii_net = cols[1], cols[2], cols[3]
        dii_buy, dii_sell, dii_net = cols[4], cols[5], cols[6]
        for _, item in frame.iterrows():
            label = str(item.get(date_col) or "")
            if "month till" in label.lower():
                continue
            day = parse_date_scalar(label.split()[0] if label else "")
            if not day:
                continue
            row = {
                "date": day,
                "source": "moneycontrol_cash",
                "variant": "cash",
                "granularity": "daily",
            }
            for col, dest in (
                (fii_buy, "fii_buy"),
                (fii_sell, "fii_sell"),
                (fii_net, "fii_net"),
                (dii_buy, "dii_buy"),
                (dii_sell, "dii_sell"),
                (dii_net, "dii_net"),
            ):
                val = _parse_money(item.get(col))
                if val is not None:
                    row[dest] = val
            if "fii_net" in row or "dii_net" in row:
                parsed.append(row)
        if parsed:
            return pd.DataFrame(parsed).sort_values("date").drop_duplicates("date", keep="last")
    return pd.DataFrame()


def parse_niftyinvest_cash_csv(text: str) -> pd.DataFrame:
    """Parse Nifty Invest capital-market CSV export."""
    if not text or len(text.strip()) < 20:
        return pd.DataFrame()
    try:
        raw = pd.read_csv(StringIO(text), encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    frame = _normalize_columns(raw)
    date_col = next((c for c in frame.columns if "date" in c or c == "d"), frame.columns[0])
    rows: list[dict[str, Any]] = []
    for _, item in frame.iterrows():
        day = parse_date_scalar(item.get(date_col))
        if not day:
            try:
                day = datetime.strptime(str(item.get(date_col))[:10], "%Y-%m-%d").date().isoformat()
            except ValueError:
                continue
        fii_net = None
        dii_net = None
        for col in frame.columns:
            label = str(col)
            val = _parse_money(item.get(col))
            if val is None:
                continue
            if "fii" in label and "net" in label:
                fii_net = val
            elif "dii" in label and "net" in label:
                dii_net = val
        if fii_net is None and dii_net is None:
            continue
        row: dict[str, Any] = {
            "date": day,
            "source": "niftyinvest_cash",
            "variant": "cash",
            "granularity": "daily",
        }
        if fii_net is not None:
            row["fii_net"] = fii_net
        if dii_net is not None:
            row["dii_net"] = dii_net
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")


def moneycontrol_cash_url(*, month: int, year: int) -> str:
    return (
        "https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/index.php"
        f"?month={month:02d}&year={year}"
    )


def niftyinvest_cash_url(*, month: int, year: int) -> str:
    return f"https://niftyinvest.com/fii-dii-data/fii-history?month={month:02d}&year={year}"
