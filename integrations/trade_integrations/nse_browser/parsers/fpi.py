"""Parsers for NSDL FPI investment activity."""

from __future__ import annotations

import re
from io import StringIO
from typing import Any

import pandas as pd

from trade_integrations.hub_storage.date_parse import parse_date_scalar
from trade_integrations.hub_storage.parquet_io import concat_dataframes, concat_frames


def _norm_cols(frame: pd.DataFrame) -> pd.DataFrame:
    mapping = {}
    for col in frame.columns:
        key = re.sub(r"[^a-z0-9]+", "_", str(col).lower()).strip("_")
        mapping[col] = key
    return frame.rename(columns=mapping)


def _parse_money(raw: str) -> float | None:
    text = str(raw or "").strip().replace(",", "")
    if not text or text in {"-", "—"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        val = float(text)
    except ValueError:
        return None
    return -val if negative else val


def parse_nsdl_fpi_html(html: str) -> pd.DataFrame:
    """Parse NSDL Latest.aspx #rpt table sub-total rows."""
    if not html:
        return pd.DataFrame()

    date_match = re.search(r"FPI Investments on (\d{2}-[A-Za-z]{3}-\d{4})", html)
    report_date = parse_date_scalar(date_match.group(1)) if date_match else None
    if not report_date:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    asset_specs = (
        ("Equity", "equity"),
        ("Debt-General Limit", "debt_general"),
        ("Debt-VRR", "debt_vrr"),
        ("Debt-FAR", "debt_far"),
        ("Hybrid", "hybrid"),
    )
    money_cell = r'<td align="right">(\([\d.]+\)|[\d.]+)</td>'
    for label, asset_key in asset_specs:
        pattern = (
            rf">{re.escape(label)}</td>.*?Sub-total</td>{money_cell}{money_cell}{money_cell}{money_cell}"
        )
        hit = re.search(pattern, html, flags=re.S)
        if not hit:
            continue
        rows.append(
            {
                "date": report_date,
                "asset_class": asset_key,
                "route": "sub_total",
                "gross_buy_inr": _parse_money(hit.group(1)),
                "gross_sell_inr": _parse_money(hit.group(2)),
                "net_inr": _parse_money(hit.group(3)),
                "net_usd": _parse_money(hit.group(4)),
                "source": "nse_browser_nsdl",
            }
        )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def parse_fpi_investment_table(frame: pd.DataFrame, *, source: str = "nselib") -> pd.DataFrame:
    """Normalize NSDL FPI investment activity to daily rows."""
    if frame is None or frame.empty:
        return pd.DataFrame()
    work = _norm_cols(frame.copy())

    date_col = next((c for c in work.columns if "report" in c or c == "date"), None)
    if date_col is None:
        date_col = work.columns[0]

    asset_col = next(
        (c for c in work.columns if "debt" in c or "equity" in c or "hybrid" in c or "asset" in c),
        None,
    )
    route_col = next((c for c in work.columns if "route" in c or "investment" in c), None)
    gross_buy = next((c for c in work.columns if "gross_purchases" in c or "buy" in c), None)
    gross_sell = next((c for c in work.columns if "gross_sales" in c or "sell" in c), None)
    net_inr = next((c for c in work.columns if "net_investment" in c and "usd" not in c), None)
    net_usd = next((c for c in work.columns if "usd" in c and "net" in c), None)

    rows: list[dict[str, Any]] = []
    for _, item in work.iterrows():
        day = parse_date_scalar(item.get(date_col))
        if not day:
            continue
        asset = str(item.get(asset_col) or "total").strip().lower().replace(" ", "_")
        row: dict[str, Any] = {
            "date": day,
            "asset_class": asset,
            "route": str(item.get(route_col) or "").strip().lower(),
            "source": source,
        }
        for src, dest in (
            (gross_buy, "gross_buy_inr"),
            (gross_sell, "gross_sell_inr"),
            (net_inr, "net_inr"),
            (net_usd, "net_usd"),
        ):
            if src and src in item.index:
                val = pd.to_numeric(item[src], errors="coerce")
                if not pd.isna(val):
                    row[dest] = float(val)
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def parse_fpi_html_tables(html: str) -> pd.DataFrame:
    """Extract FPI tables from NSDL HTML."""
    detail = parse_nsdl_fpi_html(html)
    if not detail.empty:
        return detail
    if not html:
        return pd.DataFrame()
    try:
        tables = pd.read_html(StringIO(html))
    except Exception:
        return pd.DataFrame()
    frames = [parse_fpi_investment_table(t, source="nse_browser_nsdl") for t in tables if not t.empty]
    valid = [f for f in frames if not f.empty]
    if not valid:
        return pd.DataFrame()
    return concat_frames(valid).drop_duplicates(
        subset=["date", "asset_class", "route"],
        keep="last",
    )


def aggregate_fpi_daily(frame: pd.DataFrame) -> pd.DataFrame:
    """Roll asset-class rows into one row per date with equity/debt/hybrid net columns."""
    if frame.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for day, group in frame.groupby("date"):
        row: dict[str, Any] = {"date": day, "source": "nse_browser_fpi"}
        debt_inr = 0.0
        debt_usd = 0.0
        debt_seen = False
        for _, item in group.iterrows():
            asset = str(item.get("asset_class") or "")
            net_inr = item.get("net_inr")
            net_usd = item.get("net_usd")
            if asset == "equity" or "equity" in asset:
                if net_inr is not None:
                    row["fpi_equity_net_inr"] = float(net_inr)
                if net_usd is not None:
                    row["fpi_equity_net_usd"] = float(net_usd)
            elif asset.startswith("debt") or "debt" in asset:
                debt_seen = True
                if net_inr is not None:
                    debt_inr += float(net_inr)
                if net_usd is not None:
                    debt_usd += float(net_usd)
            elif "hybrid" in asset:
                if net_inr is not None:
                    row["fpi_hybrid_net_inr"] = float(net_inr)
                if net_usd is not None:
                    row["fpi_hybrid_net_usd"] = float(net_usd)
        if debt_seen:
            row["fpi_debt_net_inr"] = debt_inr
            row["fpi_debt_net_usd"] = debt_usd
        if len(row) > 2:
            rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")
