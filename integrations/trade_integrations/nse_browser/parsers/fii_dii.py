"""CSV/JSON parsers for FII/DII NSE reports."""

from __future__ import annotations

import json
import re
from io import StringIO
from typing import Any

import pandas as pd


def _parse_nse_date(raw: Any) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    text = str(raw).strip()
    if not text:
        return None
    from datetime import datetime

    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:11], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    mapping = {}
    for col in frame.columns:
        key = re.sub(r"[^a-z0-9]+", "_", str(col).lower()).strip("_")
        mapping[col] = key
    return frame.rename(columns=mapping)


def _float_col(frame: pd.DataFrame, *names: str) -> pd.Series:
    for name in names:
        if name in frame.columns:
            cleaned = frame[name].astype(str).str.replace(",", "", regex=False)
            return pd.to_numeric(cleaned, errors="coerce")
    return pd.Series(dtype=float)


def _rows_from_category_frame(frame: pd.DataFrame, *, variant: str, source: str) -> pd.DataFrame:
    date_col = next((c for c in frame.columns if "date" in c), None)
    cat_col = next((c for c in frame.columns if "category" in c or c == "cat"), None)
    net_col = next((c for c in frame.columns if "net" in c), None)
    buy_col = next((c for c in frame.columns if "buy" in c), None)
    sell_col = next((c for c in frame.columns if "sell" in c), None)
    if not date_col or not cat_col:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for day, group in frame.groupby(date_col):
        parsed_day = _parse_nse_date(day)
        if not parsed_day:
            continue
        row: dict[str, Any] = {
            "date": parsed_day,
            "source": source,
            "variant": variant,
        }
        for _, item in group.iterrows():
            cat = str(item.get(cat_col) or "").upper()
            net = _float_col(pd.DataFrame([item]), net_col).iloc[0] if net_col else None
            buy = _float_col(pd.DataFrame([item]), buy_col).iloc[0] if buy_col else None
            sell = _float_col(pd.DataFrame([item]), sell_col).iloc[0] if sell_col else None
            if "FII" in cat or "FPI" in cat:
                if net is not None and not pd.isna(net):
                    row["fii_net"] = float(net)
                if buy is not None and not pd.isna(buy):
                    row["fii_buy"] = float(buy)
                if sell is not None and not pd.isna(sell):
                    row["fii_sell"] = float(sell)
            elif "DII" in cat:
                if net is not None and not pd.isna(net):
                    row["dii_net"] = float(net)
                if buy is not None and not pd.isna(buy):
                    row["dii_buy"] = float(buy)
                if sell is not None and not pd.isna(sell):
                    row["dii_sell"] = float(sell)
        if "fii_net" in row or "dii_net" in row:
            rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")


def parse_fii_dii_csv(text: str, *, variant: str = "combined") -> pd.DataFrame:
    """Parse NSE FII/DII CSV into daily fii_net / dii_net rows."""
    if not text or len(text.strip()) < 20:
        return pd.DataFrame()
    if text.lstrip().startswith("<"):
        return pd.DataFrame()
    try:
        raw = pd.read_csv(StringIO(text), encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()
    frame = _normalize_columns(raw)
    return _rows_from_category_frame(
        frame,
        variant=variant,
        source=f"nse_browser_{variant}",
    )


def parse_fii_dii_json(text: str) -> pd.DataFrame:
    """Parse fiidiiTradeReact JSON array into daily rows."""
    if not text or not text.strip():
        return pd.DataFrame()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return pd.DataFrame()
    if not isinstance(payload, list):
        return pd.DataFrame()
    raw = pd.DataFrame(payload)
    if raw.empty:
        return pd.DataFrame()
    frame = _normalize_columns(raw)
    return _rows_from_category_frame(frame, variant="json", source="nse_api_fiidii_react")


def merge_fii_dii_variants(*frames: pd.DataFrame) -> pd.DataFrame:
    """Merge NSE-only and combined frames; prefer combined net values."""
    valid = [f for f in frames if f is not None and not f.empty]
    if not valid:
        return pd.DataFrame()
    combined = pd.concat(valid, ignore_index=True)
    if "granularity" not in combined.columns:
        combined["granularity"] = "daily"
    else:
        combined["granularity"] = combined["granularity"].fillna("daily").astype(str)
    variant_rank = {"json": 0, "nse_only": 1, "combined": 2, "monthly": 1}
    source_rank = {
        "nse_monthly_cash": 0,
        "nse_mf_sebi_monthly": 0,
        "nse_fii_sebi_monthly": 0,
        "niftyinvest_api": 2,
        "moneycontrol_cash": 1,
        "niftyinvest_cash": 1,
        "mrchartist": 2,
        "nse_browser_nse_only": 2,
        "nse_browser_combined": 3,
        "nse_browser_csv": 3,
        "nse_api_fiidii_react": 4,
        "nse_repository": 2,
    }
    combined["_variant_rank"] = 0
    if "variant" in combined.columns:
        combined["_variant_rank"] = combined["variant"].map(lambda v: variant_rank.get(str(v), 0))
    combined["_source_rank"] = 0
    if "source" in combined.columns:
        combined["_source_rank"] = combined["source"].map(lambda s: source_rank.get(str(s), 0))
    combined = combined.sort_values(
        ["date", "granularity", "_source_rank", "_variant_rank"]
    ).drop_duplicates(["date", "granularity"], keep="last")
    return combined.drop(columns=["_variant_rank", "_source_rank"], errors="ignore").sort_values(
        "date"
    ).reset_index(drop=True)


def _parse_month_label(raw: str) -> str | None:
    """Parse 'June 2026' → ISO date (last day of month)."""
    from calendar import monthrange
    from datetime import datetime

    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%B %Y", "%b %Y"):
        try:
            dt = datetime.strptime(text, fmt)
            last = monthrange(dt.year, dt.month)[1]
            return dt.replace(day=last).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_money(raw: Any) -> float | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    text = str(raw).strip().replace(",", "")
    if not text or text in {"-", "—"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_fii_dii_monthly_cash_csv(text: str) -> pd.DataFrame:
    """
    Parse NSE monthly cash FII/DII table (Date + FII/DII gross buy/sell/net in ₹ Cr).

    Expects columns like: Date, FII gross purchase/sales/net, DII gross purchase/sales/net.
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
    date_col = next((c for c in frame.columns if "date" in c), frame.columns[0])
    fii_buy = next((c for c in frame.columns if "fii" in c and "purchase" in c), None)
    fii_sell = next((c for c in frame.columns if "fii" in c and "sales" in c), None)
    fii_net = next((c for c in frame.columns if "fii" in c and "net" in c), None)
    dii_buy = next((c for c in frame.columns if "dii" in c and "purchase" in c), None)
    dii_sell = next((c for c in frame.columns if "dii" in c and "sales" in c), None)
    dii_net = next((c for c in frame.columns if "dii" in c and "net" in c), None)

    if not fii_net and len(frame.columns) >= 7:
        cols = list(frame.columns)
        fii_buy, fii_sell, fii_net = cols[1], cols[2], cols[3]
        dii_buy, dii_sell, dii_net = cols[4], cols[5], cols[6]

    rows: list[dict[str, Any]] = []
    for _, item in frame.iterrows():
        day = _parse_month_label(item.get(date_col))
        if not day:
            continue
        row: dict[str, Any] = {
            "date": day,
            "source": "nse_monthly_cash",
            "variant": "monthly",
            "granularity": "monthly",
        }
        mapping = (
            (fii_buy, "fii_buy"),
            (fii_sell, "fii_sell"),
            (fii_net, "fii_net"),
            (dii_buy, "dii_buy"),
            (dii_sell, "dii_sell"),
            (dii_net, "dii_net"),
        )
        for col, dest in mapping:
            if col is None:
                continue
            val = _parse_money(item.get(col))
            if val is not None:
                row[dest] = val
        if "fii_net" in row or "dii_net" in row:
            rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")
