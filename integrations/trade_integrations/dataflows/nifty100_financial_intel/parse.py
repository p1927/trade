"""Parse Nifty 100 financial intelligence Excel workbooks."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

_MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    "january": "01", "february": "02", "march": "03",
    "april": "04", "june": "06", "july": "07",
    "august": "08", "september": "09", "october": "10",
    "november": "11", "december": "12",
}

_RE_ALREADY_DONE = re.compile(r"^(\d{4})-(\d{1,2})$")
_RE_FY = re.compile(r"^[Ff][Yy](\d{2,4})$")
_RE_MONTH_YEAR = re.compile(r"^([A-Za-z]+)[\s\-]+(\d{2,4})$")
_RE_BARE_YEAR = re.compile(r"^(\d{4})$")
_RE_PARTIAL = re.compile(r"^([A-Za-z]+[\s\-]\d{2,4})")
_RE_NSE_SYMBOL = re.compile(r"symbol=([A-Z0-9&\-]+)", re.I)


def normalize_year(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        raw = str(int(raw))
    value = str(raw).strip()
    if not value or value.upper() == "TTM":
        return None

    partial = _RE_PARTIAL.match(value)
    if partial and value != partial.group(1).strip():
        value = partial.group(1).strip()

    m = _RE_ALREADY_DONE.match(value)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}"

    m = _RE_FY.match(value)
    if m:
        yr = m.group(1)
        yr = yr if len(yr) == 4 else ("20" + yr if int(yr) <= 29 else "19" + yr)
        return f"{yr}-03"

    m = _RE_MONTH_YEAR.match(value)
    if m:
        month_num = _MONTH_MAP.get(m.group(1).lower())
        if not month_num:
            return None
        yr_str = m.group(2)
        yr = yr_str if len(yr_str) == 4 else ("20" + yr_str if int(yr_str) <= 29 else "19" + yr_str)
        return f"{yr}-{month_num}"

    m = _RE_BARE_YEAR.match(value)
    if m:
        return f"{m.group(1)}-03"

    return None


def _load_sheet(path: str) -> pd.DataFrame:
    raw = pd.read_excel(path)
    headers = raw.iloc[0].tolist()
    frame = raw.iloc[1:].copy()
    frame.columns = headers
    return frame


def _nse_symbol_from_url(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    m = _RE_NSE_SYMBOL.search(url)
    return m.group(1).upper() if m else None


def load_companies(path: str) -> pd.DataFrame:
    frame = _load_sheet(path)
    frame["company_id"] = frame["id"].astype(str).str.strip().str.upper()
    frame["nse_symbol"] = frame["nse_profile"].apply(_nse_symbol_from_url)
    frame["company_name"] = frame["company_name"].astype(str).str.strip()
    return frame


def _normalize_financial_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["company_id"] = out["company_id"].astype(str).str.strip().str.upper()
    out["year_norm"] = out["year"].apply(normalize_year)
    out = out[out["year_norm"].notna()].copy()
    out["year_norm"] = out["year_norm"].astype(str)
    for col in out.columns:
        if col in {"company_id", "year", "year_norm", "id"}:
            continue
        try:
            converted = pd.to_numeric(out[col])
            if converted.notna().any():
                out[col] = converted
        except (TypeError, ValueError):
            pass
    return out


def load_profitandloss(path: str) -> pd.DataFrame:
    return _normalize_financial_frame(_load_sheet(path))


def load_balancesheet(path: str) -> pd.DataFrame:
    return _normalize_financial_frame(_load_sheet(path))


def load_cashflow(path: str) -> pd.DataFrame:
    return _normalize_financial_frame(_load_sheet(path))


def load_analysis(path: str) -> pd.DataFrame:
    frame = _load_sheet(path)
    frame["company_id"] = frame["company_id"].astype(str).str.strip().str.upper()
    return frame


def build_symbol_map(companies: pd.DataFrame) -> dict[str, str]:
    """Map NSE symbol → company_id."""
    mapping: dict[str, str] = {}
    for _, row in companies.iterrows():
        cid = str(row.get("company_id") or "").strip().upper()
        nse = row.get("nse_symbol")
        if cid and isinstance(nse, str) and nse.strip():
            mapping[nse.strip().upper()] = cid
        elif cid:
            mapping[cid] = cid
    return mapping


def build_nse_lookup(companies: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Map company_id → profile metadata."""
    out: dict[str, dict[str, Any]] = {}
    for _, row in companies.iterrows():
        cid = str(row.get("company_id") or "").strip().upper()
        if not cid:
            continue
        out[cid] = {
            "company_id": cid,
            "nse_symbol": row.get("nse_symbol"),
            "company_name": row.get("company_name"),
            "face_value": row.get("face_value"),
            "book_value": row.get("book_value"),
            "roce_pct": row.get("roce_percentage"),
            "roe_pct": row.get("roe_percentage"),
        }
    return out
