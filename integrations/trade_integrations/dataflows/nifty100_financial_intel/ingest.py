"""Ingest Nifty 100 financial intelligence into hub _data and company research."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import load_company_research_json, save_company_research
from trade_integrations.dataflows.company_research.models import CompanyResearchDoc

from .config import hub_data_dir
from .fetch import cache_manifest, fetch_raw_workbooks
from .parse import (
    build_nse_lookup,
    build_symbol_map,
    load_analysis,
    load_balancesheet,
    load_cashflow,
    load_companies,
    load_profitandloss,
)
from .ratios import compute_ratios_panel, latest_ratios_by_company

logger = logging.getLogger(__name__)


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_parquet(path, index=False)
    except ImportError:
        frame.to_csv(path.with_suffix(".csv"), index=False)


def _read_parquet(path: Path) -> pd.DataFrame:
    csv_path = path.with_suffix(".csv")
    if path.is_file():
        try:
            return pd.read_parquet(path)
        except Exception:
            if csv_path.is_file():
                return pd.read_csv(csv_path)
    if csv_path.is_file():
        return pd.read_csv(csv_path)
    return pd.DataFrame()


def load_symbol_fundamentals(nse_symbol: str) -> dict[str, Any] | None:
    """Load cached Nifty 100 fundamentals for an NSE symbol from hub."""
    lookup_path = hub_data_dir() / "latest_by_nse.json"
    if not lookup_path.is_file():
        return None
    payload = json.loads(lookup_path.read_text(encoding="utf-8"))
    return payload.get(nse_symbol.strip().upper())


def _fundamentals_payload(
    *,
    nse_symbol: str,
    company_id: str,
    profile: dict[str, Any],
    latest: dict[str, Any],
    analysis_row: dict[str, Any] | None,
) -> dict[str, Any]:
    ratios = {
        "pe_ratio": None,
        "pb_ratio": None,
        "roe_pct": latest.get("roe_pct") or profile.get("roe_pct"),
        "opm_pct": latest.get("opm_pct"),
        "npm_pct": latest.get("npm_pct"),
        "roce_pct": latest.get("roce_pct") or profile.get("roce_pct"),
        "roa_pct": latest.get("roa_pct"),
        "debt_to_equity": latest.get("debt_to_equity"),
        "interest_coverage": latest.get("interest_coverage"),
        "asset_turnover": latest.get("asset_turnover"),
        "eps": latest.get("eps"),
        "face_value": profile.get("face_value"),
        "book_value": profile.get("book_value"),
    }
    ratios = {k: v for k, v in ratios.items() if v not in (None, "", [], {})}

    annual_history = {
        k: latest.get(k)
        for k in (
            "year",
            "sales_cr",
            "net_profit_cr",
            "operating_profit_cr",
            "cash_from_operations_cr",
            "free_cash_flow_cr",
            "total_assets_cr",
            "borrowings_cr",
            "equity_cr",
        )
        if latest.get(k) not in (None, "", [], {})
    }

    payload: dict[str, Any] = {
        "source": "nifty100_financial_intel",
        "primary_source": "nifty100_financial_intel",
        "company_id": company_id,
        "nse_symbol": nse_symbol,
        "company_name": profile.get("company_name"),
        "ratios": ratios,
        "annual_latest": annual_history,
        "coverage": {
            "companies": 92,
            "years": "FY2010-2024",
            "source_repo": "https://github.com/Samadhan1904/nifty100-financial-intelligence",
        },
    }
    if analysis_row:
        payload["growth_analysis"] = {
            k: analysis_row.get(k)
            for k in ("compounded_sales_growth", "compounded_profit_growth", "stock_price_cagr", "roe")
            if analysis_row.get(k) not in (None, "", [], {})
        }
    return payload


def _merge_company_research(
    nse_symbol: str,
    fundamentals: dict[str, Any],
    *,
    as_of: datetime,
) -> bool:
    existing = load_company_research_json(nse_symbol)
    if existing is None:
        doc = CompanyResearchDoc(
            ticker=nse_symbol,
            as_of=as_of,
            lookahead_days=30,
            market="IN",
            identity={
                "symbol": nse_symbol,
                "name": fundamentals.get("company_name") or nse_symbol,
                "source": "nifty100_financial_intel",
            },
            fundamentals=fundamentals,
        )
        save_company_research(doc)
        return True

    merged_fundamentals = dict(existing.fundamentals or {})
    merged_fundamentals.setdefault("sources", {})
    if isinstance(merged_fundamentals["sources"], dict):
        merged_fundamentals["sources"]["nifty100_financial_intel"] = fundamentals
    for key, value in fundamentals.items():
        if key in ("source", "primary_source", "sources"):
            continue
        if key == "ratios" and isinstance(value, dict):
            merged_fundamentals.setdefault("ratios", {}).update(value)
        elif value not in (None, "", [], {}):
            merged_fundamentals[key] = value
    merged_fundamentals["primary_source"] = merged_fundamentals.get("primary_source") or "nifty100_financial_intel"

    existing.fundamentals = merged_fundamentals
    existing.as_of = as_of
    save_company_research(existing)
    return True


def ingest_nifty100_financial_intel(
    *,
    force_fetch: bool = False,
    merge_company_research: bool = True,
) -> dict[str, Any]:
    """Fetch GitHub workbooks, compute ratios, persist hub panel, optionally merge per-symbol research."""
    paths = fetch_raw_workbooks(force=force_fetch)
    companies = load_companies(str(paths["companies"]))
    pl_df = load_profitandloss(str(paths["profitandloss"]))
    bs_df = load_balancesheet(str(paths["balancesheet"]))
    cf_df = load_cashflow(str(paths["cashflow"]))
    analysis_df = load_analysis(str(paths["analysis"]))

    panel = compute_ratios_panel(pl_df, bs_df, cf_df)
    latest_by_company = latest_ratios_by_company(panel)
    nse_lookup = build_nse_lookup(companies)
    symbol_map = build_symbol_map(companies)

    analysis_by_company: dict[str, dict[str, Any]] = {}
    for _, row in analysis_df.iterrows():
        cid = str(row.get("company_id") or "").strip().upper()
        if cid:
            analysis_by_company[cid] = row.to_dict()

    out_dir = hub_data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    panel_path = out_dir / "ratios_panel.parquet"
    _write_parquet(panel, panel_path)

    latest_by_nse: dict[str, dict[str, Any]] = {}
    for nse_symbol, company_id in symbol_map.items():
        profile = nse_lookup.get(company_id, {"company_id": company_id, "nse_symbol": nse_symbol})
        latest = latest_by_company.get(company_id, {})
        fundamentals = _fundamentals_payload(
            nse_symbol=nse_symbol,
            company_id=company_id,
            profile=profile,
            latest=latest,
            analysis_row=analysis_by_company.get(company_id),
        )
        latest_by_nse[nse_symbol] = fundamentals

    (out_dir / "latest_by_nse.json").write_text(
        json.dumps(latest_by_nse, indent=2, default=str),
        encoding="utf-8",
    )
    (out_dir / "symbol_map.json").write_text(
        json.dumps(symbol_map, indent=2, default=str),
        encoding="utf-8",
    )

    manifest = {
        **cache_manifest(paths),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "companies": len(companies),
        "panel_rows": len(panel),
        "nse_symbols": len(latest_by_nse),
        "panel_path": str(panel_path),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    merged_count = 0
    as_of = datetime.now(timezone.utc)
    if merge_company_research:
        for nse_symbol, fundamentals in latest_by_nse.items():
            try:
                if _merge_company_research(nse_symbol, fundamentals, as_of=as_of):
                    merged_count += 1
            except Exception as exc:
                logger.warning("company research merge failed for %s: %s", nse_symbol, exc)

    return {
        "status": "ok",
        "companies": len(companies),
        "panel_rows": len(panel),
        "nse_symbols": len(latest_by_nse),
        "company_research_merged": merged_count,
        "hub_dir": str(out_dir),
        "manifest": manifest,
    }


def load_ratios_panel() -> pd.DataFrame:
    """Load the hub ratios panel if present."""
    return _read_parquet(hub_data_dir() / "ratios_panel.parquet")
