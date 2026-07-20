"""Compute profitability and leverage ratios from Nifty 100 financial statements."""

from __future__ import annotations

from typing import Any

import pandas as pd


def _safe_div(numerator: Any, denominator: Any, *, multiply: float = 1.0) -> float | None:
    try:
        if numerator is None or denominator is None:
            return None
        if pd.isna(numerator) or pd.isna(denominator):
            return None
        if float(denominator) == 0:
            return None
        return round((float(numerator) / float(denominator)) * multiply, 2)
    except (TypeError, ValueError):
        return None


def _num(value: Any, default: float = 0.0) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compute_ratios_panel(
    pl_df: pd.DataFrame,
    bs_df: pd.DataFrame,
    cf_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Merge P&L, balance sheet, cash flow and compute KPI panel."""
    merged = pd.merge(
        pl_df,
        bs_df,
        on=["company_id", "year_norm"],
        how="inner",
        suffixes=("_pl", "_bs"),
    )
    if cf_df is not None and not cf_df.empty:
        merged = pd.merge(
            merged,
            cf_df[["company_id", "year_norm", "operating_activity", "investing_activity", "financing_activity", "net_cash_flow"]],
            on=["company_id", "year_norm"],
            how="left",
        )

    rows: list[dict[str, Any]] = []
    for _, row in merged.iterrows():
        eq = _num(row.get("equity_capital")) + _num(row.get("reserves"))
        dep = _num(row.get("depreciation"))
        ebit = _num(row.get("operating_profit")) - dep
        capital = eq + _num(row.get("borrowings"))
        borrow = _num(row.get("borrowings"))
        interest = _num(row.get("interest"))

        cfo = row.get("operating_activity")
        cfi = row.get("investing_activity")
        fcf = None
        if cfo is not None and not pd.isna(cfo) and cfi is not None and not pd.isna(cfi):
            fcf = round(float(cfo) + float(cfi), 2)

        rows.append(
            {
                "company_id": row["company_id"],
                "year": row["year_norm"],
                "sales_cr": row.get("sales"),
                "net_profit_cr": row.get("net_profit"),
                "operating_profit_cr": row.get("operating_profit"),
                "eps": row.get("eps"),
                "npm_pct": _safe_div(row.get("net_profit"), row.get("sales"), multiply=100),
                "opm_pct": _safe_div(row.get("operating_profit"), row.get("sales"), multiply=100),
                "roe_pct": _safe_div(row.get("net_profit"), eq, multiply=100) if eq > 0 else None,
                "roce_pct": _safe_div(ebit, capital, multiply=100) if capital > 0 else None,
                "roa_pct": _safe_div(row.get("net_profit"), row.get("total_assets"), multiply=100),
                "debt_to_equity": 0.0 if borrow == 0 else (_safe_div(borrow, eq) if eq > 0 else None),
                "interest_coverage": None if interest <= 0 else _safe_div(_num(row.get("operating_profit")) + _num(row.get("other_income")), interest),
                "asset_turnover": _safe_div(row.get("sales"), row.get("total_assets")),
                "net_debt_cr": round(borrow - _num(row.get("investments")), 2) if borrow or row.get("investments") is not None else None,
                "cash_from_operations_cr": cfo,
                "free_cash_flow_cr": fcf,
                "total_assets_cr": row.get("total_assets"),
                "borrowings_cr": row.get("borrowings"),
                "equity_cr": eq if eq else None,
            }
        )

    panel = pd.DataFrame(rows)
    if panel.empty:
        return panel
    return panel.sort_values(["company_id", "year"]).reset_index(drop=True)


def latest_ratios_by_company(panel: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Return most recent year ratios keyed by company_id."""
    if panel.empty:
        return {}
    latest = panel.sort_values("year").groupby("company_id", as_index=False).tail(1)
    out: dict[str, dict[str, Any]] = {}
    for _, row in latest.iterrows():
        cid = str(row["company_id"]).upper()
        out[cid] = {k: (None if pd.isna(v) else v) for k, v in row.to_dict().items()}
    return out
