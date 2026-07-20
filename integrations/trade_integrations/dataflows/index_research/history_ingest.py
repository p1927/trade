"""Bridge data/nse repo datasets into cold-tier history_store parquets."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.history_store import (
    load_history_dataset,
    save_history_dataset,
)
from trade_integrations.nse_browser.parsers.historic_data import load_india_macro_annual

logger = logging.getLogger(__name__)

_SOURCE_RANK: dict[str, int] = {
    "nse_sector_csv": 100,
    "historic_data_figshare": 95,
    "historic_data_constituents_nifty50": 95,
    "historic_data_xlsx": 90,
    "historic_data_archive": 85,
    "historic_data_fii_dii": 82,
    "historic_data_nifty50_fo": 58,
    "historic_data_aeron7_futures": 57,
    "historic_data_global_india_macro": 84,
    "historic_data_archive7": 83,
    "nse_repository": 80,
    "niftyinvest_api": 70,
    "vishalvx_nifty_indices": 65,
    "mrchartist": 60,
    "yfinance": 40,
    "github_datasets": 35,
    "fred": 30,
}


def _source_rank(frame: pd.DataFrame) -> pd.Series:
    if "source" not in frame.columns:
        return pd.Series(0, index=frame.index)
    return frame["source"].astype(str).map(lambda s: _SOURCE_RANK.get(s, 0)).fillna(0)


def merge_with_priority(
    frames: list[pd.DataFrame],
    *,
    on: list[str],
) -> pd.DataFrame:
    """Concat frames, prefer higher-ranked source on duplicate keys."""
    valid = [f for f in frames if f is not None and not f.empty]
    if not valid:
        return pd.DataFrame()
    combined = pd.concat(valid, ignore_index=True)
    for key in on:
        if key in combined.columns and key == "date":
            combined[key] = combined[key].astype(str).str[:10]
    combined["_rank"] = _source_rank(combined)
    combined = combined.sort_values([*on, "_rank"]).drop_duplicates(on, keep="last")
    return combined.drop(columns=["_rank"], errors="ignore").reset_index(drop=True)


def sync_historic_index_ohlcv_to_cold_tier(*, repo_root=None) -> dict[str, Any]:
    """Overlay archive NIFTY/SENSEX OHLCV into cold tier."""
    from trade_integrations.nse_browser.parsers.historic_data import load_historic_index_ohlcv
    from trade_integrations.nse_browser.repository import repo_root as default_repo_root

    root = repo_root or default_repo_root()
    results: dict[str, Any] = {}

    for index_slug, dataset in (("nifty50", "nifty_ohlcv_daily"), ("sensex", "sensex_ohlcv_daily")):
        frame = load_historic_index_ohlcv(root, index_slug)
        if frame.empty:
            results[dataset] = {"status": "skipped", "reason": "empty_frame", "dataset": dataset}
            continue
        overlay = frame.copy()
        if dataset == "nifty_ohlcv_daily":
            keep_cols = [c for c in ("date", "open", "high", "low", "close", "volume", "source") if c in overlay.columns]
            overlay = overlay[keep_cols]
        existing = load_history_dataset(dataset)
        merged = merge_with_priority([existing, overlay], on=["date"])
        results[dataset] = save_history_dataset(dataset, merged)

    return {"status": "ok", "datasets": results}


def sync_historic_constituent_ohlcv_to_cold_tier(*, repo_root=None) -> dict[str, Any]:
    """Persist archive constituent OHLCV panels into cold tier."""
    from trade_integrations.nse_browser.parsers.historic_data import load_historic_constituent_ohlcv
    from trade_integrations.nse_browser.repository import repo_root as default_repo_root

    root = repo_root or default_repo_root()
    results: dict[str, Any] = {}
    for index_slug in ("nifty50", "sensex"):
        dataset = f"{index_slug}_constituent_ohlcv_daily"
        frame = load_historic_constituent_ohlcv(root, index_slug)
        if frame.empty:
            results[dataset] = {"status": "skipped", "reason": "empty_frame", "dataset": dataset}
            continue
        results[dataset] = save_history_dataset(dataset, frame)
    return {"status": "ok", "datasets": results}


def sync_historic_figshare_to_hub(*, repo_root=None) -> dict[str, Any]:
    """Mirror Figshare weights/sectors from repo into hub nifty50 artifacts."""
    import json

    from trade_integrations.context.hub import get_hub_dir
    from trade_integrations.dataflows.external_financial_datasets.curated_ingest import (
        _read_parquet as read_hub_parquet,
        _write_parquet as write_hub_parquet,
    )
    from trade_integrations.nse_browser.parsers.historic_data import (
        load_figshare_sectors,
        load_figshare_weights,
        load_nifty50_constituents_summary,
    )
    from trade_integrations.nse_browser.repository import repo_root as default_repo_root

    root = repo_root or default_repo_root()
    wide, long = load_figshare_weights(root)
    if wide.empty and long.empty:
        return {"status": "skipped", "reason": "empty_figshare_weights"}

    hub = get_hub_dir() / "_data" / "curated_market" / "nifty50"
    hub.mkdir(parents=True, exist_ok=True)

    existing_wide = read_hub_parquet(hub / "constituents_monthly_wide.parquet")
    if not existing_wide.empty and "source" not in existing_wide.columns:
        existing_wide = existing_wide.copy()
        existing_wide["source"] = "vishalvx_nifty_indices"

    existing_long = read_hub_parquet(hub / "constituents_monthly_long.parquet")
    if not existing_long.empty and "source" not in existing_long.columns:
        existing_long = existing_long.copy()
        existing_long["source"] = "vishalvx_nifty_indices"

    combined_wide = merge_with_priority([existing_wide, wide], on=["date"]) if not existing_wide.empty else wide
    combined_long = (
        merge_with_priority([existing_long, long], on=["date", "symbol"]) if not existing_long.empty else long
    )

    wide_path = hub / "constituents_monthly_wide.parquet"
    long_path = hub / "constituents_monthly_long.parquet"
    write_hub_parquet(combined_wide, wide_path)
    write_hub_parquet(combined_long, long_path)
    save_history_dataset("nifty50_constituents_monthly_wide", combined_wide)
    save_history_dataset("nifty50_constituents_monthly_long", combined_long)

    sectors = load_figshare_sectors(root)
    sectors_result: dict[str, Any] = {"status": "skipped"}
    if not sectors.empty:
        sectors_path = hub / "sector_map.parquet"
        write_hub_parquet(sectors, sectors_path)
        save_history_dataset("nifty50_sectors", sectors)
        sectors_result = {"status": "ok", "rows": len(sectors), "path": str(sectors_path)}

    summary = load_nifty50_constituents_summary(root)
    summary_result: dict[str, Any] = {"status": "skipped"}
    if not summary.empty:
        summary_result = save_history_dataset("nifty50_constituents_membership_summary", summary)

    latest = combined_wide.sort_values("date").iloc[-1]
    day = str(latest["date"])
    symbols = [
        str(col).upper()
        for col in combined_wide.columns
        if col not in {"date", "source"} and pd.notna(latest.get(col)) and float(latest[col]) > 0
    ]
    current_path = hub / "constituents_current.json"
    current_path.write_text(
        json.dumps(
            {
                "as_of": day,
                "source": "historic_data_figshare",
                "symbols": symbols,
                "count": len(symbols),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "status": "ok",
        "months": len(combined_wide),
        "membership_rows": len(combined_long),
        "symbols_tracked": len([c for c in combined_wide.columns if c not in {"date", "source"}]),
        "start": str(combined_wide["date"].iloc[0]),
        "end": str(combined_wide["date"].iloc[-1]),
        "wide_path": str(wide_path),
        "long_path": str(long_path),
        "sectors": sectors_result,
        "membership_summary": summary_result,
        "current_constituents": str(current_path),
    }


def sync_india_macro_annual_to_cold_tier(*, repo_root=None) -> dict[str, Any]:
    """Persist historic_data annual macro into cold tier."""
    from trade_integrations.nse_browser.repository import repo_root as default_repo_root

    root = repo_root or default_repo_root()
    frame = load_india_macro_annual(root)
    if frame.empty:
        return {"status": "skipped", "reason": "empty_frame", "dataset": "india_macro_annual"}
    if "date" not in frame.columns and "year" in frame.columns:
        frame = frame.copy()
        frame["date"] = frame["year"].astype(str) + "-12-31"
    return save_history_dataset("india_macro_annual", frame)


def sync_sector_indices_to_cold_tier() -> dict[str, Any]:
    """Copy repo sector index OHLC into cold tier."""
    from trade_integrations.nse_browser.repository import load_sector_indices_frame

    frame = load_sector_indices_frame()
    if frame.empty:
        return {"status": "skipped", "reason": "empty_frame", "dataset": "sector_index_daily"}
    return save_history_dataset("sector_index_daily", frame)


def sync_repo_flows_to_cold_tier(*, start: str = "2006-01-01", end: str | None = None) -> dict[str, Any]:
    """Merge repo FII/DII into flow_cash_daily cold tier."""
    from datetime import datetime, timezone

    from trade_integrations.nse_browser.repository import load_nse_repository_fii_dii_frame

    def _daily_flow_only(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        out = frame.copy()
        out["date"] = out["date"].astype(str).str[:10]
        if "granularity" in out.columns:
            out = out[out["granularity"].astype(str) != "monthly"]
        return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)

    end_day = (end or datetime.now(timezone.utc).date().isoformat())[:10]
    repo = _daily_flow_only(load_nse_repository_fii_dii_frame(start, end_day))
    existing = _daily_flow_only(load_history_dataset("flow_cash_daily"))

    cash = merge_with_priority([existing, repo], on=["date"])
    if cash.empty:
        return {"status": "skipped", "reason": "empty_frame", "dataset": "flow_cash_daily"}

    from trade_integrations.nse_browser.parsers.structural_adjustments import (
        adjust_institutional_flow_expiry_settlement,
    )

    cash = adjust_institutional_flow_expiry_settlement(cash)

    cash_result = save_history_dataset("flow_cash_daily", cash)

    deriv_cols = [
        c
        for c in cash.columns
        if c
        not in {
            "date",
            "source",
            "fii_net",
            "dii_net",
            "fii_buy",
            "fii_sell",
            "dii_buy",
            "dii_sell",
            "granularity",
            "variant",
            "fii_net_raw",
            "dii_net_raw",
            "is_fo_monthly_expiry",
        }
    ]
    deriv_result: dict[str, Any] = {"status": "skipped"}
    if deriv_cols:
        repo_deriv = cash[["date"] + deriv_cols].copy()
        if "source" not in repo_deriv.columns and "source" in cash.columns:
            repo_deriv["source"] = cash["source"]
        existing_deriv = load_history_dataset("flow_derivatives_daily")
        from trade_integrations.nse_browser.parsers.fii_dii import overlay_derivative_columns

        deriv = overlay_derivative_columns(existing_deriv, repo_deriv)
        deriv_result = save_history_dataset("flow_derivatives_daily", deriv)

    return {"status": "ok", "cash": cash_result, "derivatives": deriv_result}


def sync_global_india_macro_to_cold_tier(*, repo_root=None) -> dict[str, Any]:
    """Merge global-india-markets-macro daily series into macro_daily and nifty OHLCV."""
    from trade_integrations.nse_browser.parsers.historic_data import load_global_india_daily_macro
    from trade_integrations.nse_browser.repository import repo_root as default_repo_root

    root = repo_root or default_repo_root()
    frame = load_global_india_daily_macro(root)
    if frame.empty:
        return {"status": "skipped", "reason": "empty_frame", "dataset": "global_india_daily_macro"}

    results: dict[str, Any] = {}
    macro_cols = [
        c
        for c in ("date", "nifty_close", "sp500", "usd_inr", "gold", "oil_brent", "source")
        if c in frame.columns
    ]
    macro_overlay = frame[macro_cols].copy()
    existing_macro = load_history_dataset("macro_daily")
    merged_macro = merge_with_priority([existing_macro, macro_overlay], on=["date"])
    results["macro_daily"] = save_history_dataset("macro_daily", merged_macro)

    if "nifty_close" in frame.columns:
        nifty_overlay = frame[["date", "nifty_close"]].rename(columns={"nifty_close": "close"}).copy()
        nifty_overlay["source"] = frame["source"]
        existing_nifty = load_history_dataset("nifty_ohlcv_daily")
        merged_nifty = merge_with_priority([existing_nifty, nifty_overlay], on=["date"])
        results["nifty_ohlcv_daily"] = save_history_dataset("nifty_ohlcv_daily", merged_nifty)

    return {"status": "ok", "datasets": results}


def sync_historic_intraday_to_cold_tier(*, repo_root=None) -> dict[str, Any]:
    """Persist Nifty 50 intraday bars from archive (4) into cold tier."""
    from trade_integrations.nse_browser.parsers.historic_data import load_nifty_intraday
    from trade_integrations.nse_browser.repository import repo_root as default_repo_root

    root = repo_root or default_repo_root()
    results: dict[str, Any] = {}
    for interval in ("5min", "30min"):
        dataset = f"nifty50_intraday_{interval}"
        frame = load_nifty_intraday(root, interval)
        if frame.empty:
            results[dataset] = {"status": "skipped", "reason": "empty_frame", "dataset": dataset}
            continue
        results[dataset] = save_history_dataset(dataset, frame)
    return {"status": "ok", "datasets": results}


def sync_india_cpi_to_cold_tier(*, repo_root=None) -> dict[str, Any]:
    """Persist india CPI monthly YoY from repo into cold tier."""
    from trade_integrations.nse_browser.repository import repo_root as default_repo_root

    root = repo_root or default_repo_root()
    path = root / "india_cpi_monthly_yoy.parquet"
    if not path.is_file():
        return {"status": "skipped", "reason": "missing_file", "dataset": "india_cpi_monthly_yoy"}
    frame = pd.read_parquet(path)
    if frame.empty:
        return {"status": "skipped", "reason": "empty_frame", "dataset": "india_cpi_monthly_yoy"}
    return save_history_dataset("india_cpi_monthly_yoy", frame)


def sync_india_rbi_wss_to_cold_tier(*, repo_root=None) -> dict[str, Any]:
    """Persist RBI WSS weekly rates from repo into cold tier."""
    from trade_integrations.nse_browser.repository import repo_root as default_repo_root

    root = repo_root or default_repo_root()
    path = root / "india_rbi_wss_weekly.parquet"
    if not path.is_file():
        return {"status": "skipped", "reason": "missing_file", "dataset": "india_rbi_wss_weekly"}
    frame = pd.read_parquet(path)
    if frame.empty:
        return {"status": "skipped", "reason": "empty_frame", "dataset": "india_rbi_wss_weekly"}
    return save_history_dataset("india_rbi_wss_weekly", frame)


def sync_historic_news_to_cold_tier(*, repo_root=None) -> dict[str, Any]:
    """Persist aggregated news sentiment from historic_data into cold tier."""
    from trade_integrations.nse_browser.parsers.historic_data import load_india_news_sentiment_daily
    from trade_integrations.nse_browser.repository import repo_root as default_repo_root

    root = repo_root or default_repo_root()
    frame = load_india_news_sentiment_daily(root)
    if frame.empty:
        return {"status": "skipped", "reason": "empty_frame", "dataset": "india_news_sentiment_daily"}
    return save_history_dataset("india_news_sentiment_daily", frame)


def sync_nifty_ohlcv_overlay(*, start: str = "2006-01-01", end: str | None = None) -> dict[str, Any]:
    """Overlay NSE sector CSV Nifty closes onto existing nifty_ohlcv_daily."""
    from datetime import datetime, timezone

    from trade_integrations.nse_browser.repository import load_sector_indices_frame

    end_day = (end or datetime.now(timezone.utc).date().isoformat())[:10]
    sector = load_sector_indices_frame(start=start[:10], end=end_day)
    if sector.empty:
        return {"status": "skipped", "reason": "no_sector_data", "dataset": "nifty_ohlcv_daily"}

    nifty = sector[sector["index_slug"] == "nifty50"].copy()
    if nifty.empty or "close" not in nifty.columns:
        return {"status": "skipped", "reason": "no_nifty50_rows", "dataset": "nifty_ohlcv_daily"}

    overlay = nifty[["date", "open", "high", "low", "close"]].copy()
    overlay["source"] = "nse_sector_csv"
    existing = load_history_dataset("nifty_ohlcv_daily")
    merged = merge_with_priority([existing, overlay], on=["date"])
    if merged.empty:
        return {"status": "skipped", "reason": "empty_merge", "dataset": "nifty_ohlcv_daily"}
    return save_history_dataset("nifty_ohlcv_daily", merged)


def sync_repo_to_cold_tier(
    *,
    start: str = "2006-01-01",
    end: str | None = None,
    include_macro_backfill: bool = True,
    include_flow_backfill: bool = True,
    allow_live_fetch: bool = False,
) -> dict[str, Any]:
    """Full repo → cold-tier sync: historic_data, flows, sector, macro API backfills."""
    results: dict[str, Any] = {}

    results["india_macro_annual"] = sync_india_macro_annual_to_cold_tier()
    results["historic_index_ohlcv"] = sync_historic_index_ohlcv_to_cold_tier()
    results["historic_constituent_ohlcv"] = sync_historic_constituent_ohlcv_to_cold_tier()
    results["historic_figshare_hub"] = sync_historic_figshare_to_hub()
    results["global_india_macro"] = sync_global_india_macro_to_cold_tier()
    results["historic_intraday"] = sync_historic_intraday_to_cold_tier()
    results["historic_news"] = sync_historic_news_to_cold_tier()
    results["india_cpi"] = sync_india_cpi_to_cold_tier()
    results["india_rbi_wss"] = sync_india_rbi_wss_to_cold_tier()
    results["sector_index_daily"] = sync_sector_indices_to_cold_tier()
    results["repo_flows"] = sync_repo_flows_to_cold_tier(start=start, end=end)

    if include_macro_backfill:
        from trade_integrations.dataflows.index_research.sources.historical_macro import backfill_macro_history

        results["macro_backfill"] = backfill_macro_history(start=start, end=end, dry_run=False)
        results["nifty_ohlcv_overlay"] = sync_nifty_ohlcv_overlay(start=start, end=end)

    if include_flow_backfill:
        from trade_integrations.dataflows.index_research.sources.historical_flows import backfill_flow_history

        results["flow_backfill"] = backfill_flow_history(
            start=start,
            end=end,
            allow_live_fetch=allow_live_fetch,
            dry_run=False,
        )

    from trade_integrations.dataflows.github_datasets import ingest_github_macro_datasets

    results["github_macro_datasets"] = ingest_github_macro_datasets(
        force_fetch=False,
        merge_macro_daily=True,
    )

    from trade_integrations.dataflows.external_financial_datasets import ingest_external_financial_datasets

    results["external_financial_datasets"] = ingest_external_financial_datasets(
        force_fetch=False,
        include_huggingface=True,
        include_kaggle=True,
    )

    from trade_integrations.dataflows.external_financial_datasets.curated_ingest import ingest_curated_market_data

    results["curated_market_data"] = ingest_curated_market_data(force_fetch=False, include_kaggle=True)

    return {"status": "ok", "datasets": results}
