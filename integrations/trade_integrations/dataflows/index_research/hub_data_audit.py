"""Audit hub data completeness for prediction miss root-cause analysis."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents
from trade_integrations.dataflows.index_research.factor_matrix import MACRO_FACTOR_KEYS
from trade_integrations.dataflows.index_research.factor_store import get_factor_data_dir, load_factor_history
from trade_integrations.dataflows.index_research.horizon_dates import resolve_maturity_trading_date
from trade_integrations.dataflows.index_research.sources.history_loader import load_aligned_factor_history

_ANOMALOUS_STEMS = frozenset({"None", "none", "null", "NaT"})


def _audit_report_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "data_audit_latest.json"


def save_data_audit_report(report: dict[str, Any], *, ticker: str = "NIFTY") -> Path:
    path = _audit_report_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def load_data_audit_report(ticker: str = "NIFTY") -> dict[str, Any] | None:
    path = _audit_report_path(ticker)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _trading_date_on_or_before(dates: list[str], target: date) -> str | None:
    eligible = [d for d in dates if d <= target.isoformat()]
    return eligible[-1] if eligible else None


def _maturity_date(prediction_date: str, horizon_days: int, trading_dates: list[str]) -> str | None:
    return resolve_maturity_trading_date(prediction_date, horizon_days, trading_dates)


def _constituent_history_coverage(hub: Path, trading_days: list[str]) -> dict[str, Any]:
    constituents = [row.symbol.strip().upper() for row in load_nifty50_constituents()]
    if not constituents or not trading_days:
        return {
            "constituent_count": len(constituents),
            "trading_days": len(trading_days),
            "mean_coverage_pct": 0.0,
            "by_symbol": {},
        }

    by_symbol: dict[str, dict[str, Any]] = {}
    daily_totals: list[int] = []
    for sym in constituents:
        history_dir = hub / sym / "company_research" / "history"
        available = set()
        if history_dir.is_dir():
            for path in history_dir.glob("*.json"):
                stem = path.stem[:10]
                if len(stem) == 10 and stem[4] == "-":
                    available.add(stem)
        hits = sum(1 for day in trading_days if day in available)
        pct = 100.0 * hits / len(trading_days) if trading_days else 0.0
        by_symbol[sym] = {"history_days": len(available), "coverage_pct": round(pct, 1)}
        daily_totals.append(hits)

    mean_day_coverage = (
        100.0 * sum(daily_totals) / (len(trading_days) * len(constituents))
        if trading_days and constituents
        else 0.0
    )
    return {
        "constituent_count": len(constituents),
        "trading_days": len(trading_days),
        "mean_coverage_pct": round(mean_day_coverage, 1),
        "by_symbol": by_symbol,
    }


def _factor_coverage_from_daily(start: str, end: str, *, flow_era_start: str | None = None) -> list[dict[str, Any]]:
    long_df = load_factor_history(start, end)
    if long_df.empty or "factor" not in long_df.columns:
        return []

    dates = sorted(long_df["date"].astype(str).str[:10].unique())
    day_count = max(1, len(dates))
    era_dates = [d for d in dates if flow_era_start is None or d >= flow_era_start[:10]]
    era_day_count = max(1, len(era_dates))
    rows: list[dict[str, Any]] = []
    for factor in sorted(long_df["factor"].astype(str).unique()):
        sub = long_df[long_df["factor"].astype(str) == factor]
        present_days = sub["date"].astype(str).str[:10].nunique()
        era_present = (
            sub[sub["date"].astype(str).str[:10].isin(era_dates)]["date"].astype(str).str[:10].nunique()
            if era_dates
            else present_days
        )
        rows.append(
            {
                "factor": factor,
                "days_present": int(present_days),
                "days_total": day_count,
                "coverage_pct": round(100.0 * present_days / day_count, 1),
                "flow_era_days_present": int(era_present),
                "flow_era_days_total": era_day_count,
                "flow_era_coverage_pct": round(100.0 * era_present / era_day_count, 1),
                "in_macro_keys": factor in MACRO_FACTOR_KEYS,
            }
        )
    return rows


def _anomalous_snapshots() -> list[str]:
    out_dir = get_factor_data_dir()
    if not out_dir.is_dir():
        return []
    bad: list[str] = []
    for path in out_dir.iterdir():
        if path.suffix not in {".csv", ".parquet"}:
            continue
        if path.stem in _ANOMALOUS_STEMS or len(path.stem) != 10 or path.stem[4] != "-":
            bad.append(path.name)
    return sorted(bad)


def _nse_browser_source_coverage() -> dict[str, Any]:
    """Coverage summary for nodriver-persisted NSE/NSDL datasets."""
    try:
        from trade_integrations.nse_browser.hub_writer import (
            load_fii_dii_daily,
            load_fpi_daily,
            load_mission_status,
        )
    except ImportError:
        return {"available": False}

    fii = load_fii_dii_daily()
    fpi = load_fpi_daily()
    missions = {
        mid: load_mission_status(mid)
        for mid in ("fii_dii_history", "fpi_nsdl", "market_archives")
    }
    return {
        "available": True,
        "fii_dii_daily_rows": len(fii),
        "fii_net_days": int(fii["fii_net"].notna().sum()) if not fii.empty and "fii_net" in fii.columns else 0,
        "dii_net_days": int(fii["dii_net"].notna().sum()) if not fii.empty and "dii_net" in fii.columns else 0,
        "fpi_daily_rows": len(fpi),
        "missions": missions,
    }


def run_hub_data_audit(
    *,
    days: int = 365,
    horizon_days: int = 14,
    ticker: str = "NIFTY",
) -> dict[str, Any]:
    """Build hub data completeness report for T0/T1 prediction RCA."""
    hub = get_hub_dir()
    aligned = load_aligned_factor_history(days=days)
    trading_dates = aligned["date"].astype(str).str[:10].tolist() if not aligned.empty else []

    start = trading_dates[0] if trading_dates else ""
    end = trading_dates[-1] if trading_dates else ""

    flow_era_start = None
    try:
        from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
            flow_effective_start,
            flow_backfill_summary,
            merge_flow_derivatives_frame,
        )

        if start and end:
            merged = merge_flow_derivatives_frame(start, end)
            flow_era_start = flow_effective_start(merged)
    except Exception:
        flow_era_start = None

    factor_coverage = (
        _factor_coverage_from_daily(start, end, flow_era_start=flow_era_start) if start and end else []
    )
    constituent_coverage = _constituent_history_coverage(hub, trading_dates)

    t0_t1_gaps: list[dict[str, Any]] = []
    if trading_dates:
        for pred_day in trading_dates[:: max(1, len(trading_dates) // 20)]:
            maturity = _maturity_date(pred_day, horizon_days, trading_dates)
            if not maturity:
                t0_t1_gaps.append(
                    {
                        "prediction_date": pred_day,
                        "maturity_date": None,
                        "issue": "no_maturity_trading_day",
                    }
                )
                continue
            t0_row = aligned[aligned["date"].astype(str).str[:10] == pred_day]
            t1_row = aligned[aligned["date"].astype(str).str[:10] == maturity]
            missing_at_t0 = []
            missing_at_t1 = []
            for key in MACRO_FACTOR_KEYS:
                if key not in aligned.columns:
                    continue
                if t0_row.empty or pd.isna(t0_row.iloc[0].get(key)):
                    missing_at_t0.append(key)
                if t1_row.empty or pd.isna(t1_row.iloc[0].get(key)):
                    missing_at_t1.append(key)
            if missing_at_t0 or missing_at_t1:
                t0_t1_gaps.append(
                    {
                        "prediction_date": pred_day,
                        "maturity_date": maturity,
                        "missing_factors_t0": missing_at_t0[:8],
                        "missing_factors_t1": missing_at_t1[:8],
                    }
                )

    macro_gaps = [
        f
        for f in factor_coverage
        if f.get("in_macro_keys")
        and (f.get("flow_era_coverage_pct") or f.get("coverage_pct") or 0) < 90
        and f.get("factor") in {"fii_net_5d", "dii_net_5d", "nifty_pcr", "fii_fut_long_short_ratio"}
    ]

    blocking = bool(t0_t1_gaps) or bool(_anomalous_snapshots())

    return {
        "status": "ok",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker.strip().upper(),
        "window_days": days,
        "horizon_days": horizon_days,
        "history_start": start,
        "history_end": end,
        "flow_effective_start": flow_era_start,
        "trading_rows": len(trading_dates),
        "factor_coverage": factor_coverage,
        "macro_factor_gaps": macro_gaps,
        "constituent_history": constituent_coverage,
        "t0_t1_availability_gaps": t0_t1_gaps[:30],
        "anomalous_snapshots": _anomalous_snapshots(),
        "blocking_gaps": blocking,
        "recommendations": _recommendations(
            macro_gaps=macro_gaps,
            constituent_mean=constituent_coverage.get("mean_coverage_pct") or 0,
            anomalous=_anomalous_snapshots(),
            nse_browser=_nse_browser_source_coverage(),
        ),
        "nse_browser": _nse_browser_source_coverage(),
    }


def _recommendations(
    *,
    macro_gaps: list[dict[str, Any]],
    constituent_mean: float,
    anomalous: list[str],
    nse_browser: dict[str, Any] | None = None,
) -> list[str]:
    notes: list[str] = []
    if macro_gaps:
        keys = ", ".join(str(g.get("factor")) for g in macro_gaps[:5])
        notes.append(f"Run enrich_factor_history for low-coverage macro keys: {keys}")
        if any(str(g.get("factor")) in {"fii_net_5d", "dii_net_5d"} for g in macro_gaps):
            notes.append(
                "Run get_nse_browser_data(dataset='fii_dii', refresh=true) "
                "or scripts/run_prediction_data_backfill.py --days 365"
            )
    nse = nse_browser or {}
    fii_rows = int(nse.get("fii_dii_daily_rows") or 0)
    if nse.get("available") and fii_rows < 30:
        notes.append(
            "nse_browser hub sparse — get_nse_browser_data(dataset='fii_dii', refresh=true) after market close"
        )
    if constituent_mean < 30:
        notes.append(
            "Constituent company_research history sparse — run backfill_nifty_constituent_news for eval dates"
        )
    if anomalous:
        notes.append(f"Remove or fix anomalous factor daily files: {', '.join(anomalous)}")
    if not notes:
        notes.append("Hub has sufficient T0/T1 factor coverage for macro horizon diff analysis")
    return notes


def run_and_save_data_audit(**kwargs: Any) -> dict[str, Any]:
    report = run_hub_data_audit(**kwargs)
    if report.get("status") == "ok":
        save_data_audit_report(report, ticker=str(kwargs.get("ticker") or "NIFTY"))
    return report
