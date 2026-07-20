"""Ingest GitHub macro datasets into hub cold tier and merge into macro_daily."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.history_store import (
    load_history_dataset,
    save_history_dataset,
)

from .config import DATASETS, SOURCE_NAME, hub_data_dir
from .fetch import cache_manifest, fetch_all
from .parse import (
    expand_to_daily,
    factor_series,
    parse_exchange_rates_daily,
    parse_gold_monthly,
    parse_oil_daily,
    parse_us_10y_monthly,
    parse_us_cpi,
    parse_us_gdp_quarter,
    parse_vix_daily,
)

logger = logging.getLogger(__name__)

_MACRO_MERGE_FACTORS: tuple[tuple[str, str], ...] = (
    ("us_10y", "github_us_10y_daily"),
    ("gold", "github_gold_daily"),
    ("usd_inr", "github_usd_inr_daily"),
    ("vix", "github_vix_daily"),
    ("oil_brent", "github_oil_brent_daily"),
    ("oil_wti", "github_oil_wti_daily"),
)

_COLD_TIER_ONLY: tuple[tuple[str, str], ...] = (
    ("us_cpi", "github_us_cpi_monthly"),
    ("us_gdp", "github_us_gdp_quarterly"),
)


def _write_parquet(frame: pd.DataFrame, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_parquet(path, index=False)
    except ImportError:
        frame.to_csv(path.with_suffix(".csv"), index=False)


def _merge_factor_into_macro(
    macro: pd.DataFrame,
    github_daily: pd.DataFrame,
    factor: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Merge one factor into macro_daily dates only: existing/yfinance wins; github fills gaps."""
    stats: dict[str, Any] = {
        "factor": factor,
        "github_rows": 0,
        "filled_gaps": 0,
        "overridden_by_existing": 0,
    }

    github_map = factor_series(github_daily, factor)
    if github_map.empty:
        stats["status"] = "skipped"
        return macro, stats

    stats["github_rows"] = len(github_map)

    if macro.empty:
        out = pd.DataFrame({"date": github_map.index.astype(str), factor: github_map.values})
        stats["filled_gaps"] = len(out)
        stats["status"] = "seeded"
        stats["merge_policy"] = "no existing macro_daily — seeded from github"
        return out, stats

    macro = macro.copy()
    macro["date"] = macro["date"].astype(str).str[:10]
    if factor not in macro.columns:
        macro[factor] = pd.NA

    filled = 0
    overridden = 0
    merged_vals: list[Any] = []
    for day, cur in zip(macro["date"], macro[factor], strict=False):
        gh = github_map.get(day)
        if pd.notna(cur):
            merged_vals.append(cur)
            if gh is not None and abs(float(cur) - float(gh)) > 1e-4:
                overridden += 1
        elif gh is not None:
            merged_vals.append(gh)
            filled += 1
        else:
            merged_vals.append(pd.NA)
    macro[factor] = merged_vals

    stats["filled_gaps"] = filled
    stats["overridden_by_existing"] = overridden
    stats["status"] = "merged"
    stats["coverage_pct"] = round(float(macro[factor].notna().mean()) * 100.0, 1) if len(macro) else 0.0
    stats["merge_policy"] = "existing/yfinance wins on overlap; github fills gaps on macro_daily dates only"
    stats["note"] = "Full github history kept in github_* cold-tier datasets"
    return macro, stats


def verify_github_macro_merge() -> dict[str, Any]:
    """Report github dataset presence, cold-tier rows, and macro_daily merge coverage."""
    hub_dir = hub_data_dir()
    manifest_path = hub_dir / "manifest.json"
    report: dict[str, Any] = {
        "hub_dir": str(hub_dir),
        "manifest_exists": manifest_path.is_file(),
        "macro_merge_factors": {},
        "cold_tier_only": {},
    }

    if manifest_path.is_file():
        report["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))

    macro = load_history_dataset("macro_daily")
    report["macro_daily_rows"] = len(macro)

    for factor, cold_name in _MACRO_MERGE_FACTORS:
        cold = load_history_dataset(cold_name)
        factor_report: dict[str, Any] = {
            "cold_tier_dataset": cold_name,
            "cold_tier_rows": len(cold),
            "merged_into_macro_daily": factor in (macro.columns if not macro.empty else []),
        }
        if not macro.empty and factor in macro.columns:
            non_null = int(macro[factor].notna().sum())
            factor_report["macro_non_null"] = non_null
            factor_report["macro_coverage_pct"] = round(non_null / len(macro) * 100.0, 1)
            if not cold.empty and factor in cold.columns:
                both = macro[macro[factor].notna()].merge(
                    cold[["date", factor]].rename(columns={factor: f"{factor}_github"}),
                    on="date",
                    how="inner",
                )
                if not both.empty:
                    diffs = (both[factor].astype(float) - both[f"{factor}_github"].astype(float)).abs()
                    factor_report["overlap_days"] = len(both)
                    factor_report["max_abs_diff_on_overlap"] = round(float(diffs.max()), 4)
                    factor_report["merge_policy"] = "existing/yfinance wins on overlap; github fills gaps"
        else:
            factor_report["macro_non_null"] = 0
            factor_report["macro_coverage_pct"] = 0.0
        report["macro_merge_factors"][factor] = factor_report

    for _key, cold_name in _COLD_TIER_ONLY:
        cold = load_history_dataset(cold_name)
        report["cold_tier_only"][cold_name] = {
            "rows": len(cold),
            "merged_into_macro_daily": False,
            "note": "Stored for attribution/backfill; not a Ridge factor column today",
        }

    return report


def ingest_github_macro_datasets(
    *,
    force_fetch: bool = False,
    merge_macro_daily: bool = True,
) -> dict[str, Any]:
    """Fetch GitHub CSVs, persist cold-tier panels, optionally enrich macro_daily."""
    paths = fetch_all(force=force_fetch)

    us10y_monthly = parse_us_10y_monthly(str(paths["us_10y"]))
    gold_monthly = parse_gold_monthly(str(paths["gold"]))
    fx_wide = parse_exchange_rates_daily(str(paths["exchange_rates_daily"]))
    vix_daily = parse_vix_daily(str(paths["vix_daily"]))
    brent_daily = parse_oil_daily(str(paths["oil_brent_daily"]), column="oil_brent")
    wti_daily = parse_oil_daily(str(paths["oil_wti_daily"]), column="oil_wti")
    us_cpi = parse_us_cpi(str(paths["us_cpi"]))
    us_gdp = parse_us_gdp_quarter(str(paths["us_gdp_quarter"]))

    us10y_daily = expand_to_daily(us10y_monthly, ["us_10y"])
    gold_daily = expand_to_daily(gold_monthly, ["gold"])

    out_dir = hub_data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_parquet(vix_daily, out_dir / "vix_daily.parquet")
    _write_parquet(brent_daily, out_dir / "oil_brent_daily.parquet")
    _write_parquet(wti_daily, out_dir / "oil_wti_daily.parquet")
    _write_parquet(us_cpi, out_dir / "us_cpi_monthly.parquet")
    _write_parquet(us_gdp, out_dir / "us_gdp_quarterly.parquet")
    _write_parquet(fx_wide, out_dir / "fx_daily_wide.parquet")

    cold_results: dict[str, Any] = {}
    cold_results["github_us_10y_daily"] = save_history_dataset("github_us_10y_daily", us10y_daily)
    cold_results["github_gold_daily"] = save_history_dataset("github_gold_daily", gold_daily)
    cold_results["github_vix_daily"] = save_history_dataset("github_vix_daily", vix_daily)
    cold_results["github_oil_brent_daily"] = save_history_dataset("github_oil_brent_daily", brent_daily)
    cold_results["github_oil_wti_daily"] = save_history_dataset("github_oil_wti_daily", wti_daily)
    cold_results["github_us_cpi_monthly"] = save_history_dataset("github_us_cpi_monthly", us_cpi)
    cold_results["github_us_gdp_quarterly"] = save_history_dataset("github_us_gdp_quarterly", us_gdp)

    if "usd_inr" in fx_wide.columns:
        inr = fx_wide[["date", "usd_inr"]].copy()
        inr["source"] = SOURCE_NAME
        inr = inr.dropna(subset=["usd_inr"])
        cold_results["github_usd_inr_daily"] = save_history_dataset("github_usd_inr_daily", inr)

    merge_stats: dict[str, Any] = {}
    macro_result: dict[str, Any] = {"status": "skipped"}

    if merge_macro_daily:
        macro = load_history_dataset("macro_daily")
        merge_frames = (
            ("us_10y", us10y_daily),
            ("gold", gold_daily),
            ("usd_inr", fx_wide),
            ("vix", vix_daily),
            ("oil_brent", brent_daily),
            ("oil_wti", wti_daily),
        )
        for factor, github_frame in merge_frames:
            macro, stats = _merge_factor_into_macro(macro, github_frame, factor)
            merge_stats[factor] = stats

        if not macro.empty:
            macro_result = save_history_dataset("macro_daily", macro)

    source_urls = sorted({str(spec["source_url"]) for spec in DATASETS})

    manifest = {
        **cache_manifest(paths),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "vix_daily_rows": len(vix_daily),
        "oil_brent_daily_rows": len(brent_daily),
        "oil_wti_daily_rows": len(wti_daily),
        "us_cpi_rows": len(us_cpi),
        "us_gdp_rows": len(us_gdp),
        "cold_tier": cold_results,
        "merge_stats": merge_stats,
        "hub_dir": str(out_dir),
        "curated_from": "https://github.com/awesomedata/awesome-public-datasets + https://github.com/datasets",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    verification = verify_github_macro_merge()

    return {
        "status": "ok",
        "sources": source_urls + ["https://github.com/datasets/finance-vix"],
        "cold_tier": cold_results,
        "macro_daily": macro_result,
        "merge_stats": merge_stats,
        "verification": verification,
        "manifest": manifest,
    }
