"""Bridge data/nse repo datasets into cold-tier history_store parquets."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from trade_integrations.hub_storage.parquet_io import concat_dataframes, concat_frames

from trade_integrations.dataflows.index_research.history_store import (
    load_history_dataset,
    save_history_dataset,
)
from trade_integrations.nse_browser.parsers.fii_dii import _DERIVATIVE_COLUMNS
from trade_integrations.nse_browser.parsers.historic_data import load_india_macro_annual

logger = logging.getLogger(__name__)

_FLOW_DERIVATIVE_COLUMNS = frozenset(_DERIVATIVE_COLUMNS)

_FLOW_CASH_BASE_COLUMNS = frozenset(
    {
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
        "fii_net_settlement_adj",
        "dii_net_settlement_adj",
    }
)


def _flow_derivative_column_names(columns) -> list[str]:
    return [c for c in columns if c in _FLOW_DERIVATIVE_COLUMNS]


def _unknown_flow_cash_columns(columns) -> list[str]:
    return [c for c in columns if c not in _FLOW_CASH_BASE_COLUMNS and c not in _FLOW_DERIVATIVE_COLUMNS]


def _flow_cash_only_frame(frame: pd.DataFrame) -> pd.DataFrame:
    unknown = _unknown_flow_cash_columns(frame.columns)
    if unknown:
        logger.warning("dropping unknown flow_cash_daily columns: %s", unknown)
    keep = [c for c in frame.columns if c in _FLOW_CASH_BASE_COLUMNS]
    return frame[keep].copy()


def _persist_flow_cash_cold_tier(cash: pd.DataFrame, *, overlay: pd.DataFrame) -> dict[str, Any]:
    """Migrate deriv cols, guard against window-only wipe, save cash-only cold tier."""
    existing = load_history_dataset("flow_cash_daily")
    prior_rows = len(existing)
    deriv_result = _sync_flow_derivatives_daily(cash)
    stripped = _flow_cash_only_frame(cash)
    if prior_rows > 0 and len(stripped) < max(1, int(prior_rows * 0.5)):
        return {
            "status": "error",
            "reason": "flow_cash_shrink_guard",
            "prior_rows": prior_rows,
            "new_rows": len(stripped),
            "derivatives": deriv_result,
        }
    cash_result = save_history_dataset("flow_cash_daily", stripped, merge=False)
    return {
        "status": _flow_cold_tier_save_status(overlay, deriv_result, cash_result),
        "cash": cash_result,
        "derivatives": deriv_result,
    }


def _prepare_existing_flow_cash(existing: pd.DataFrame) -> pd.DataFrame:
    """Migrate legacy derivative columns off flow_cash_daily rows."""
    if existing.empty or not _flow_derivative_column_names(existing.columns):
        return existing
    deriv_result = _sync_flow_derivatives_daily(existing)
    if deriv_result.get("status") not in {"ok", "skipped"}:
        return existing
    return _flow_cash_only_frame(existing)


def _flow_cold_tier_save_status(
    overlay: pd.DataFrame,
    deriv_result: dict[str, Any],
    cash_result: dict[str, Any] | None = None,
) -> str:
    if cash_result and cash_result.get("status") == "error":
        return "error"
    if deriv_result.get("status") == "ok":
        return "ok"
    if _flow_derivative_column_names(overlay.columns):
        return "partial"
    return "ok"


def _sync_flow_derivatives_daily(overlay: pd.DataFrame) -> dict[str, Any]:
    deriv_cols = _flow_derivative_column_names(overlay.columns)
    if not deriv_cols:
        return {"status": "skipped"}
    deriv_frame = overlay[["date"] + [c for c in deriv_cols if c in overlay.columns]].copy()
    if "source" not in deriv_frame.columns and "source" in overlay.columns:
        deriv_frame["source"] = overlay["source"]
    from trade_integrations.nse_browser.parsers.fii_dii import overlay_derivative_columns

    existing_deriv = load_history_dataset("flow_derivatives_daily")
    deriv = overlay_derivative_columns(existing_deriv, deriv_frame)
    return save_history_dataset("flow_derivatives_daily", deriv)


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


def _frames_for_concat(frames: list[pd.DataFrame]) -> list[pd.DataFrame]:
    """Drop empty frames and all-NA columns before concat (pandas 2.x compat)."""
    out: list[pd.DataFrame] = []
    for frame in frames:
        if frame is None or frame.empty:
            continue
        trimmed = frame.dropna(how="all", axis=1)
        if trimmed.empty:
            continue
        out.append(trimmed)
    return out


def merge_with_priority(
    frames: list[pd.DataFrame],
    *,
    on: list[str],
) -> pd.DataFrame:
    """Concat frames, prefer higher-ranked source on duplicate keys."""
    valid = _frames_for_concat(frames)
    if not valid:
        return pd.DataFrame()
    combined = concat_frames(valid)
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
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.nse_browser.repository import load_nse_repository_fii_dii_frame

    def _daily_flow_only(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        out = frame.copy()
        out["date"] = out["date"].astype(str).str[:10]
        if "granularity" in out.columns:
            out = out[out["granularity"].astype(str) != "monthly"]
        return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)

    end_day = (end or india_trading_date_iso())[:10]
    repo = _daily_flow_only(load_nse_repository_fii_dii_frame(start, end_day))
    existing = _prepare_existing_flow_cash(_daily_flow_only(load_history_dataset("flow_cash_daily")))

    cash = merge_with_priority([existing, repo], on=["date"])
    if cash.empty:
        return {"status": "skipped", "reason": "empty_frame", "dataset": "flow_cash_daily"}

    from trade_integrations.nse_browser.parsers.structural_adjustments import (
        adjust_institutional_flow_expiry_settlement,
    )

    cash = adjust_institutional_flow_expiry_settlement(cash)

    return _persist_flow_cash_cold_tier(cash, overlay=cash)


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
    from trade_integrations.nse_browser.parsers.historic_data import historic_data_dir
    from trade_integrations.nse_browser.repository import repo_root as default_repo_root

    root = repo_root or default_repo_root()
    path = historic_data_dir(root) / "india_cpi_monthly_yoy.parquet"
    if not path.is_file():
        return {"status": "skipped", "reason": "missing_file", "dataset": "india_cpi_monthly_yoy"}
    frame = pd.read_parquet(path)
    if frame.empty:
        return {"status": "skipped", "reason": "empty_frame", "dataset": "india_cpi_monthly_yoy"}
    return save_history_dataset("india_cpi_monthly_yoy", frame)


def sync_india_rbi_wss_to_cold_tier(*, repo_root=None) -> dict[str, Any]:
    """Persist RBI WSS weekly rates from repo into cold tier."""
    from trade_integrations.nse_browser.parsers.historic_data import historic_data_dir
    from trade_integrations.nse_browser.repository import repo_root as default_repo_root

    root = repo_root or default_repo_root()
    path = historic_data_dir(root) / "india_rbi_wss_weekly.parquet"
    if not path.is_file():
        return {"status": "skipped", "reason": "missing_file", "dataset": "india_rbi_wss_weekly"}
    frame = pd.read_parquet(path)
    if frame.empty:
        return {"status": "skipped", "reason": "empty_frame", "dataset": "india_rbi_wss_weekly"}
    return save_history_dataset("india_rbi_wss_weekly", frame)


def sync_india_credit_spread_to_cold_tier(*, repo_root=None) -> dict[str, Any]:
    """Persist CRISIL / corporate credit spread series from repo into cold tier."""
    from trade_integrations.nse_browser.parsers.historic_data import historic_data_dir
    from trade_integrations.nse_browser.repository import repo_root as default_repo_root

    root = repo_root or default_repo_root()
    path = historic_data_dir(root) / "india_credit_spread_daily.parquet"
    if not path.is_file():
        return {"status": "skipped", "reason": "missing_file", "dataset": "india_credit_spread_daily"}
    frame = pd.read_parquet(path)
    if frame.empty or "date" not in frame.columns:
        return {"status": "skipped", "reason": "empty_frame", "dataset": "india_credit_spread_daily"}
    col = None
    for candidate in ("india_credit_spread", "credit_spread", "baa_aaa_spread", "spread"):
        if candidate in frame.columns:
            col = candidate
            break
    if col is None:
        return {"status": "skipped", "reason": "missing_spread_column", "dataset": "india_credit_spread_daily"}
    out = frame[["date", col]].copy()
    if col != "india_credit_spread":
        out = out.rename(columns={col: "india_credit_spread"})
    return save_history_dataset("india_credit_spread_daily", out)


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
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.nse_browser.repository import load_sector_indices_frame

    end_day = (end or india_trading_date_iso())[:10]
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


def sync_valuation_to_cold_tier() -> dict[str, Any]:
    """Refresh nifty50_valuation_daily from repo historic_data drop (PE/PB/div yield)."""
    try:
        from trade_integrations.nse_browser.repository import repo_root

        repo_path = repo_root() / "historic_data" / "nifty50_valuation_daily.parquet"
        if not repo_path.is_file():
            return {"status": "skipped", "reason": "missing_repo_parquet", "dataset": "nifty50_valuation_daily"}
        frame = pd.read_parquet(repo_path)
        if frame.empty or "date" not in frame.columns:
            return {"status": "skipped", "reason": "empty_frame", "dataset": "nifty50_valuation_daily"}
        existing = load_history_dataset("nifty50_valuation_daily")
        if not existing.empty:
            from trade_integrations.dataflows.index_research.history_ingest import merge_with_priority

            frame = merge_with_priority([existing, frame], on=["date"])
        return save_history_dataset("nifty50_valuation_daily", frame, merge=True)
    except Exception as exc:
        return {"status": "error", "error": str(exc), "dataset": "nifty50_valuation_daily"}


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
    results["india_credit_spread_daily"] = sync_india_credit_spread_to_cold_tier()
    results["nifty50_valuation_daily"] = sync_valuation_to_cold_tier()
    results["sector_index_daily"] = sync_sector_indices_to_cold_tier()
    results["historic_derivatives"] = sync_historic_derivatives_to_cold_tier(start=start, end=end)
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


def sync_historic_derivatives_to_cold_tier(
    *,
    start: str = "2006-01-01",
    end: str | None = None,
) -> dict[str, Any]:
    """Merge historic OI / FO bhavcopy / MrChartist JSON into flow_derivatives_daily."""
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
        load_fo_bhavcopy_derivatives_frame,
        load_nifty_oi_daily_frame,
        load_participant_oi_cache_frame,
    )
    from trade_integrations.nse_browser.parsers.fii_dii import overlay_derivative_columns
    from trade_integrations.nse_browser.parsers.historic_data import (
        historic_data_dir,
        parse_mrchartist_history_json,
    )
    from trade_integrations.nse_browser.repository import repo_root

    end_day = (end or india_trading_date_iso())[:10]
    existing = load_history_dataset("flow_derivatives_daily")
    merged = existing.copy() if not existing.empty else pd.DataFrame()

    for loader in (
        lambda: load_nifty_oi_daily_frame(start=start, end=end_day),
        lambda: load_fo_bhavcopy_derivatives_frame(start=start, end=end_day),
    ):
        frame = loader()
        if frame is not None and not frame.empty:
            merged = overlay_derivative_columns(merged, frame)

    json_path = historic_data_dir(repo_root()) / "mrchartist_history_full.json"
    if json_path.is_file():
        mr = parse_mrchartist_history_json(json_path)
        if not mr.empty:
            mr = mr[(mr["date"] >= start[:10]) & (mr["date"] <= end_day)]
            merged = overlay_derivative_columns(merged, mr)

    poi = load_participant_oi_cache_frame(start=start[:10], end=end_day)
    if poi is not None and not poi.empty:
        merged = overlay_derivative_columns(merged, poi)

    if merged.empty:
        return {"status": "skipped", "reason": "empty_frame", "dataset": "flow_derivatives_daily"}

    result = save_history_dataset("flow_derivatives_daily", merged)
    pcr_days = int(merged["nifty_pcr"].notna().sum()) if "nifty_pcr" in merged.columns else 0
    return {"status": "ok", "pcr_days": pcr_days, **result}


def history_incremental_sync_enabled() -> bool:
    import os

    return os.getenv("HISTORY_INCREMENTAL_SYNC", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def sync_index_ohlcv_via_data_router(*, start: str, end: str) -> dict[str, Any]:
    """Optional recent index OHLCV overlay via DataRouter when enabled."""
    from trade_integrations.data_router import data_router_enabled, fetch
    from trade_integrations.data_router.types import FetchSpec

    if not data_router_enabled():
        return {"status": "skipped", "reason": "DATA_ROUTER_ENABLED off"}

    results: dict[str, Any] = {}
    for symbol, dataset in (("^NSEI", "nifty_ohlcv_daily"), ("^BSESN", "sensex_ohlcv_daily")):
        spec = FetchSpec(
            domain="ohlcv",
            market="india_equity",
            symbol=symbol,
            start=start[:10],
            end=end[:10],
        )
        result = fetch(spec)
        if result.status != "ok" or result.data is None or getattr(result.data, "empty", True):
            results[dataset] = {
                "status": "skipped",
                "reason": result.status,
                "attempts": [a.name for a in (result.attempts or [])],
            }
            continue
        overlay = result.data.copy()
        overlay["source"] = str(result.source_id or "data_router")
        keep_cols = [c for c in ("date", "open", "high", "low", "close", "volume", "source") if c in overlay.columns]
        overlay = overlay[keep_cols]
        existing = load_history_dataset(dataset)
        merged = merge_with_priority([existing, overlay], on=["date"])
        results[dataset] = save_history_dataset(dataset, merged)
    return {"status": "ok", "datasets": results}


def sync_nifty_ohlcv_tail(*, end: str | None = None) -> dict[str, Any]:
    """Refresh trailing Nifty daily bars when cold-tier cache is behind the trading calendar."""
    from trade_integrations.dataflows.index_research.sources.history_loader import (
        refresh_nifty_history_tail_if_stale,
    )

    return refresh_nifty_history_tail_if_stale(end=end)


def run_history_incremental_sync(*, days: int = 30, explicit: bool = False) -> dict[str, Any]:
    """Lightweight append: recent flows/derivatives/sector + hub mirror (no yfinance 2006 refetch)."""
    from datetime import date, timedelta

    from trade_integrations.dataflows.company_research.market import india_trading_date_iso

    if not history_incremental_sync_enabled():
        return {"status": "skipped", "reason": "HISTORY_INCREMENTAL_SYNC disabled"}

    end_day = date.fromisoformat(india_trading_date_iso()[:10])
    end = end_day.isoformat()
    start = (end_day - timedelta(days=max(1, days))).isoformat()
    results: dict[str, Any] = {
        "start": start,
        "end": end,
        "sector_index_daily": sync_sector_indices_to_cold_tier(),
        "historic_derivatives": sync_historic_derivatives_to_cold_tier(start=start, end=end),
        "repo_flows": sync_repo_flows_to_cold_tier(start=start, end=end),
    }
    try:
        from trade_integrations.data_router import data_router_enabled

        if data_router_enabled():
            results["data_router_index_ohlcv"] = sync_index_ohlcv_via_data_router(start=start, end=end)
    except Exception as exc:
        results["data_router_index_ohlcv"] = {"status": "error", "error": str(exc)}
    try:
        from trade_integrations.nse_browser.repository import ingest_repository_to_hub

        results["hub"] = ingest_repository_to_hub(
            allow_live_fetch=False,
            explicit=explicit,
            skip_repo_sync=True,
        )
    except Exception as exc:
        results["hub"] = {"status": "error", "error": str(exc)}
    return {"status": "ok", "datasets": results}


_OHLCV_FACTOR_MAP = (
    ("open", "nifty_open"),
    ("high", "nifty_high"),
    ("low", "nifty_low"),
    ("close", "nifty_close"),
    ("volume", "nifty_volume"),
)


def upsert_ohlcv_daily_factors(trading_day: str) -> dict[str, Any]:
    """Mirror cold-tier Nifty OHLCV for *trading_day* into the daily factor store."""
    from trade_integrations.dataflows.index_research.factor_store import upsert_daily_factors

    day = str(trading_day)[:10]
    ohlcv = load_history_dataset("nifty_ohlcv_daily")
    if ohlcv.empty or "date" not in ohlcv.columns:
        return {"status": "skipped", "reason": "no_ohlcv", "day": day}
    row_frame = ohlcv[ohlcv["date"].astype(str).str[:10] == day]
    if row_frame.empty:
        return {"status": "skipped", "reason": "no_row_for_day", "day": day}
    bar = row_frame.iloc[-1]
    rows: list[dict[str, Any]] = []
    for col, factor in _OHLCV_FACTOR_MAP:
        if col not in bar.index:
            continue
        val = bar[col]
        if pd.isna(val):
            continue
        try:
            rows.append(
                {
                    "factor": factor,
                    "value": float(val),
                    "source": str(bar.get("source") or "nifty_ohlcv_daily"),
                }
            )
        except (TypeError, ValueError):
            continue
    if not rows:
        return {"status": "skipped", "reason": "no_ohlc_fields", "day": day}
    upsert_daily_factors(day, rows)
    return {"status": "ok", "day": day, "factors": [row["factor"] for row in rows]}


def persist_daily_hub_market_data() -> dict[str, Any]:
    """Refresh trailing Nifty OHLCV and mirror open/close into daily factor files."""
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso

    trading_day = india_trading_date_iso()[:10]
    ohlcv_result = sync_nifty_ohlcv_tail(end=trading_day)

    ohlcv = load_history_dataset("nifty_ohlcv_daily")
    factor_days: list[str] = [trading_day]
    if not ohlcv.empty and "date" in ohlcv.columns:
        max_date = str(ohlcv["date"].astype(str).str[:10].max())
        if max_date not in factor_days:
            factor_days.append(max_date)

    daily_factors = [upsert_ohlcv_daily_factors(day) for day in sorted(set(factor_days))]
    ohlcv_status = str((ohlcv_result or {}).get("status") or "ok")
    factor_errors = [
        item for item in daily_factors if isinstance(item, dict) and item.get("status") == "error"
    ]
    top_status = "error" if ohlcv_status == "error" or factor_errors else "ok"
    return {
        "status": top_status,
        "trading_day": trading_day,
        "ohlcv": ohlcv_result,
        "daily_factors": daily_factors,
    }


def sync_flow_cache_to_cold_tier() -> dict[str, Any]:
    """Promote merged flow cache rows into cold-tier flow parquets."""
    from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
        load_flow_cash_cache,
    )
    from trade_integrations.nse_browser.parsers.structural_adjustments import (
        adjust_institutional_flow_expiry_settlement,
    )

    cache = load_flow_cash_cache()
    if cache.empty or "date" not in cache.columns:
        return {"status": "skipped", "reason": "empty_cache"}

    cache = cache.copy()
    cache["date"] = cache["date"].astype(str).str[:10]

    existing_cash = _prepare_existing_flow_cash(load_history_dataset("flow_cash_daily"))
    cash = merge_with_priority([existing_cash, cache], on=["date"])
    if cash.empty:
        return {"status": "skipped", "reason": "empty_cash_merge"}

    cash = adjust_institutional_flow_expiry_settlement(cash)
    return _persist_flow_cash_cold_tier(cash, overlay=cash)


def sync_macro_daily_tail(*, days: int = 14) -> dict[str, Any]:
    """Fetch recent macro series and merge into ``macro_daily`` cold tier."""
    from datetime import date, timedelta

    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.dataflows.index_research.sources.historical_macro import (
        build_macro_daily_tail_frame,
        load_nifty_ohlcv_tail_frame,
    )

    end = india_trading_date_iso()[:10]
    start = (date.fromisoformat(end) - timedelta(days=max(1, days))).isoformat()

    nifty_tail = load_nifty_ohlcv_tail_frame(start=start, end=end)
    if not nifty_tail.empty:
        existing_nifty = load_history_dataset("nifty_ohlcv_daily")
        merged_nifty = (
            merge_with_priority([existing_nifty, nifty_tail], on=["date"])
            if not existing_nifty.empty
            else nifty_tail
        )
        save_history_dataset("nifty_ohlcv_daily", merged_nifty)

    tail = build_macro_daily_tail_frame(start=start, end=end)
    if tail.empty:
        return {"status": "skipped", "reason": "empty_macro_tail", "tail_start": start, "tail_end": end}

    macro_out = tail.drop(
        columns=[c for c in ("open", "high", "low", "volume") if c in tail.columns],
        errors="ignore",
    )
    existing = load_history_dataset("macro_daily")
    merged = merge_with_priority([existing, macro_out], on=["date"]) if not existing.empty else macro_out
    result = save_history_dataset("macro_daily", merged)
    return {"status": "ok", "tail_start": start, "tail_end": end, "dataset": result}


def sync_india_vix_tail(*, days: int = 14) -> dict[str, Any]:
    """Fetch recent India VIX and merge into ``india_vix_daily`` cold tier."""
    from datetime import date, timedelta

    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.dataflows.index_research.sources.historical_flows import (
        fetch_india_vix_history,
    )

    end = india_trading_date_iso()[:10]
    start = (date.fromisoformat(end) - timedelta(days=max(1, days))).isoformat()
    vix = fetch_india_vix_history(start=start, end=end)
    if vix.empty:
        return {"status": "skipped", "reason": "empty_vix", "start": start, "end": end}
    existing = load_history_dataset("india_vix_daily")
    merged = merge_with_priority([existing, vix], on=["date"]) if not existing.empty else vix
    result = save_history_dataset("india_vix_daily", merged)
    return {"status": "ok", "tail_start": start, "tail_end": end, "dataset": result}


def _cold_tier_step_unhealthy(name: str, step: dict[str, Any], *, repo_flows: dict[str, Any]) -> bool:
    status = step.get("status")
    if status == "error" or status == "partial":
        return True
    if status != "skipped":
        return False
    if name == "panel":
        return True
    if name == "cache_flows":
        return step.get("reason") == "empty_cache" and repo_flows.get("status") != "ok"
    if name == "macro_daily":
        return step.get("reason") == "empty_macro_tail"
    if name == "india_vix_daily":
        return step.get("reason") == "empty_vix"
    return False


def finalize_daily_cold_tier(
    *,
    flow_lookback_days: int = 7,
    macro_lookback_days: int = 14,
    panel_tail_days: int = 14,
) -> dict[str, Any]:
    """After live fetches + enrich: promote all rows into cold tier and refresh panel."""
    from datetime import date, timedelta

    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.dataflows.index_research.history_panel import refresh_panel_tail

    trading_day = india_trading_date_iso()[:10]
    flow_start = (
        date.fromisoformat(trading_day) - timedelta(days=max(1, flow_lookback_days))
    ).isoformat()

    repo_flows = sync_repo_flows_to_cold_tier(start=flow_start, end=trading_day)
    cache_flows = sync_flow_cache_to_cold_tier()
    macro = sync_macro_daily_tail(days=macro_lookback_days)
    vix = sync_india_vix_tail(days=macro_lookback_days)
    panel = refresh_panel_tail(days=panel_tail_days)

    steps = {
        "repo_flows": repo_flows,
        "cache_flows": cache_flows,
        "macro_daily": macro,
        "india_vix_daily": vix,
        "panel": panel,
    }
    failed_steps = [
        name
        for name, step in steps.items()
        if isinstance(step, dict) and _cold_tier_step_unhealthy(name, step, repo_flows=repo_flows)
    ]
    return {
        "status": "partial" if failed_steps else "ok",
        "failed_steps": failed_steps,
        "trading_day": trading_day,
        **steps,
    }
