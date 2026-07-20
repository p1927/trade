"""Curated market data ingest — Nifty 50, FII/DII, macro events, constituents."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.history_store import (
    load_history_dataset,
    save_history_dataset,
)
from trade_integrations.dataflows.throttled_http import fetch_delay_sec, fetch_to_path

import pandas as pd

from trade_integrations.hub_storage.parquet_io import concat_dataframes, concat_frames
from trade_integrations.http import HTTPError

logger = logging.getLogger(__name__)

_UA = "trade-stack-research/0.1 (+https://github.com/p1927/trade)"
HUB_SUBDIR = "_data/curated_market"

KAGGLE_NIFTY50_VALUATION = os.environ.get(
    "KAGGLE_NIFTY50_VALUATION_DATASET",
    "obiwankanobi/nifty-50-historical-pe-pb-div-yield-eps-and-close",
)


def hub_dir() -> Path:
    return get_hub_dir() / HUB_SUBDIR


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


def _parse_nse_date(raw: Any) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    text = str(raw).strip()
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _fetch_raw(url: str, dest: Path, *, force: bool = False, max_retries: int | None = None, timeout: float = 180) -> Path:
    return fetch_to_path(url, dest, force=force, timeout=timeout, max_retries=max_retries)


def _fetch_raw_optional(url: str, dest: Path, *, force: bool = False) -> bool:
    """Fetch when present; return False on 404 without raising."""
    try:
        _fetch_raw(url, dest, force=force, max_retries=1)
        return True
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return False
        raise
    except RuntimeError as exc:
        if "404" in str(exc):
            return False
        raise


def _normalize_valuation_frame(frame: pd.DataFrame) -> pd.DataFrame:
    col_map = {c.lower().replace(" ", "_"): c for c in frame.columns}
    out = frame.copy()
    rename: dict[str, str] = {}
    for key, orig in col_map.items():
        if key in {"date", "datetime"}:
            rename[orig] = "date"
        elif key in {"p/e", "pe", "p_e", "trailing_pe"}:
            rename[orig] = "nifty_pe"
        elif key in {"p/b", "pb", "p_b"}:
            rename[orig] = "nifty_pb"
        elif "div" in key and "yield" in key:
            rename[orig] = "nifty_dividend_yield"
        elif key in {"close", "index_close", "nifty_close"}:
            rename[orig] = "nifty_close"
        elif key == "eps":
            rename[orig] = "nifty_eps"
    out = out.rename(columns=rename)
    if "date" not in out.columns:
        return pd.DataFrame()
    out["date"] = out["date"].apply(_parse_nse_date)
    out = out[out["date"].notna()].copy()
    for col in ("nifty_pe", "nifty_pb", "nifty_dividend_yield", "nifty_close", "nifty_eps"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["source"] = out.get("source", "curated_market")
    keep = [c for c in ("date", "nifty_pe", "nifty_pb", "nifty_dividend_yield", "nifty_close", "nifty_eps", "source") if c in out.columns]
    return out[keep].drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def _merge_valuation_panels(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """Merge valuation panels; incoming (local/NSE) wins on overlapping dates."""
    if existing.empty:
        return incoming.copy()
    if incoming.empty:
        return existing.copy()
    left = existing.copy()
    right = incoming.copy()
    left["date"] = left["date"].astype(str).str[:10]
    right["date"] = right["date"].astype(str).str[:10]
    merged = left.set_index("date")
    right_idx = right.set_index("date")
    for col in right_idx.columns:
        if col not in merged.columns:
            merged[col] = pd.NA
        merged[col] = right_idx[col].combine_first(merged[col])
    return merged.reset_index().sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def _merge_valuation_into_macro(panel: pd.DataFrame, *, prefer_incoming: bool = True) -> dict[str, Any]:
    macro = load_history_dataset("macro_daily")
    if macro.empty or panel.empty:
        return {"status": "skipped"}
    macro = macro.copy()
    macro["date"] = macro["date"].astype(str).str[:10]
    val_map = panel.set_index("date")
    for col in ("nifty_pe", "nifty_pb", "nifty_dividend_yield", "nifty_eps"):
        if col not in panel.columns:
            continue
        series = val_map[col].to_dict()
        if col not in macro.columns:
            macro[col] = pd.NA
        merged_vals: list[Any] = []
        for day, cur in zip(macro["date"], macro[col], strict=False):
            incoming = series.get(day)
            if prefer_incoming and incoming is not None and pd.notna(incoming):
                merged_vals.append(incoming)
            elif pd.notna(cur):
                merged_vals.append(cur)
            elif incoming is not None and pd.notna(incoming):
                merged_vals.append(incoming)
            else:
                merged_vals.append(pd.NA)
        macro[col] = merged_vals
    return save_history_dataset("macro_daily", macro)


def ingest_nifty50_valuation_local() -> dict[str, Any]:
    """Ingest Nifty 50 PE/PB/Div from data/nse/historic_data/ local CSV(s)."""
    from trade_integrations.nse_browser.parsers.historic_data import (
        _merge_nifty50_valuation_frames,
        discover_nifty50_valuation_csvs,
        parse_nifty50_pe_pb_div_csv,
    )
    from trade_integrations.nse_browser.repository import repo_root

    paths = discover_nifty50_valuation_csvs(repo_root())
    if not paths:
        return {"status": "skipped", "reason": "missing_local_valuation_csv"}

    frames = [parse_nifty50_pe_pb_div_csv(path) for path in paths]
    panel = _merge_nifty50_valuation_frames(frames)
    if panel.empty:
        return {"status": "error", "reason": "empty_local_valuation_panel", "paths": [str(p) for p in paths]}

    out_path = hub_dir() / "nifty50" / "valuation_daily.parquet"
    existing = _read_parquet(out_path)
    combined = _merge_valuation_panels(existing, panel)
    _write_parquet(combined, out_path)
    cold = save_history_dataset("nifty50_valuation_daily", combined)
    macro_result = _merge_valuation_into_macro(panel, prefer_incoming=True)

    return {
        "status": "ok",
        "sources": [path.name for path in paths],
        "rows": len(panel),
        "combined_rows": len(combined),
        "start": str(combined["date"].iloc[0]),
        "end": str(combined["date"].iloc[-1]),
        "cold_tier": cold,
        "macro_daily": macro_result,
        "merged_into_macro_daily": macro_result.get("status") == "ok",
    }


def ingest_nifty50_constituents_local() -> dict[str, Any]:
    """Ingest current Nifty 50 list from data/nse/historic_data/ind_nifty50list.csv."""
    from trade_integrations.nse_browser.parsers.historic_data import (
        local_ind_nifty50_list_csv_path,
        parse_ind_nifty50_list_csv,
    )
    from trade_integrations.nse_browser.repository import repo_root

    path = local_ind_nifty50_list_csv_path(repo_root())
    if path is None:
        return {"status": "skipped", "reason": "missing_ind_nifty50list_csv"}

    payload = parse_ind_nifty50_list_csv(path)
    if payload.get("status") != "ok":
        return payload

    out = hub_dir() / "nifty50" / "constituents_current.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    cache_dest = hub_dir() / "cache" / "niftyindices" / "ind_nifty50list.csv"
    cache_dest.parent.mkdir(parents=True, exist_ok=True)
    cache_dest.write_bytes(path.read_bytes())

    return payload


def ingest_nifty50_valuation_github(*, force_fetch: bool = False) -> dict[str, Any]:
    """Nifty 50 PE/PB/Div from RSwarnkar/nifty50-scrapping (NSE-sourced archive on GitHub)."""
    cache = hub_dir() / "cache" / "nifty50_scrapping"
    cache.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []

    base_url = "https://raw.githubusercontent.com/RSwarnkar/nifty50-scrapping/master/data/ratio_data_immutable"
    for year in range(1999, datetime.now().year + 1):
        dest = cache / f"ratio_{year}.csv"
        try:
            if not _fetch_raw_optional(f"{base_url}/{year}.csv", dest, force=force_fetch):
                continue
            raw = pd.read_csv(dest)
            norm = _normalize_valuation_frame(raw)
            if not norm.empty:
                norm["source"] = "github_nifty50_scrapping"
                frames.append(norm)
        except Exception as exc:
            logger.debug("ratio year %s skipped: %s", year, exc)

    basic_dest = cache / "basic-data-2000-2026.csv"
    try:
        _fetch_raw(
            "https://raw.githubusercontent.com/RSwarnkar/nifty50-scrapping/master/output/basic-data-2000-2026.csv",
            basic_dest,
            force=force_fetch,
        )
        basic = pd.read_csv(basic_dest)
        basic = basic.rename(columns={"Date": "date", "Close": "nifty_close"})
        basic["date"] = basic["date"].apply(_parse_nse_date)
        basic["nifty_close"] = pd.to_numeric(basic["nifty_close"], errors="coerce")
        basic = basic[["date", "nifty_close"]].dropna()
        basic["source"] = "github_nifty50_scrapping"
        if frames:
            merged = concat_frames(frames)
            merged = merged.merge(basic, on="date", how="outer", suffixes=("", "_basic"))
            if "nifty_close_basic" in merged.columns:
                merged["nifty_close"] = merged["nifty_close"].combine_first(merged["nifty_close_basic"])
                merged = merged.drop(columns=["nifty_close_basic"])
            panel = merged
        else:
            panel = basic
    except Exception as exc:
        logger.warning("basic Nifty OHLC merge failed: %s", exc)
        panel = concat_frames(frames) if frames else pd.DataFrame()

    if panel.empty:
        return {"status": "error", "reason": "empty_valuation_panel"}

    panel = panel.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    out_path = hub_dir() / "nifty50" / "valuation_daily.parquet"
    _write_parquet(panel, out_path)
    cold = save_history_dataset("nifty50_valuation_daily", panel)

    macro_result: dict[str, Any] = {"status": "skipped"}
    macro = load_history_dataset("macro_daily")
    if not macro.empty:
        macro_result = _merge_valuation_into_macro(panel, prefer_incoming=False)

    return {
        "status": "ok",
        "source": "https://github.com/RSwarnkar/nifty50-scrapping",
        "rows": len(panel),
        "start": str(panel["date"].iloc[0]),
        "end": str(panel["date"].iloc[-1]),
        "cold_tier": cold,
        "macro_daily": macro_result,
        "kaggle_alternative": KAGGLE_NIFTY50_VALUATION,
        "note": "Kaggle obiwankanobi dataset used when KAGGLE credentials configured",
    }


def ingest_nifty50_valuation_kaggle() -> dict[str, Any]:
    """Optional Kaggle Nifty 50 PE/PB/Div (obiwankanobi)."""
    kaggle_json = Path.home() / ".kaggle" / "kaggle.json"
    has_env = bool(os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"))
    if not kaggle_json.is_file() and not has_env:
        return {"status": "skipped", "reason": "no_kaggle_credentials", "slug": KAGGLE_NIFTY50_VALUATION}

    try:
        import kagglehub
    except ImportError:
        return {"status": "skipped", "reason": "kagglehub_not_installed", "slug": KAGGLE_NIFTY50_VALUATION}

    cache = hub_dir() / "cache" / "kaggle" / "nifty50_valuation"
    cache.mkdir(parents=True, exist_ok=True)
    try:
        path = Path(kagglehub.dataset_download(KAGGLE_NIFTY50_VALUATION, path=cache))
    except Exception as exc:
        return {"status": "error", "slug": KAGGLE_NIFTY50_VALUATION, "error": str(exc)}

    csv_files = list(path.rglob("*.csv"))
    if not csv_files:
        return {"status": "error", "reason": "no_csv_in_dataset", "path": str(path)}

    raw = pd.read_csv(csv_files[0])
    panel = _normalize_valuation_frame(raw)
    if panel.empty:
        return {"status": "error", "reason": "unparseable_csv", "file": str(csv_files[0])}

    panel["source"] = "kaggle_nifty50_valuation"
    out_path = hub_dir() / "nifty50" / "valuation_daily_kaggle.parquet"
    _write_parquet(panel, out_path)
    return {"status": "ok", "slug": KAGGLE_NIFTY50_VALUATION, "rows": len(panel), "path": str(out_path)}


def ingest_nifty50_constituents_historical(*, force_fetch: bool = False) -> dict[str, Any]:
    """Monthly Nifty 50 membership panel (2008–present) from vishalvx/nifty-indices-datasets."""
    cache = hub_dir() / "cache" / "vishalvx"
    dest = cache / "nifty50_weights.csv"
    url = "https://raw.githubusercontent.com/vishalvx/nifty-indices-datasets/main/datasets/nifty50_weights.csv"
    _fetch_raw(url, dest, force=force_fetch)
    wide = pd.read_csv(dest)
    wide = wide.rename(columns={"DATE": "date"})
    wide["date"] = pd.to_datetime(wide["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    wide = wide.dropna(subset=["date"]).sort_values("date")

    long_rows: list[dict[str, Any]] = []
    symbol_cols = [c for c in wide.columns if c != "date"]
    for _, row in wide.iterrows():
        day = row["date"]
        for sym in symbol_cols:
            val = row.get(sym)
            if pd.notna(val) and float(val) > 0:
                long_rows.append({"date": day, "symbol": str(sym).upper(), "in_index": 1.0, "source": "vishalvx_nifty_indices"})

    long_panel = pd.DataFrame(long_rows)
    out_wide = hub_dir() / "nifty50" / "constituents_monthly_wide.parquet"
    out_long = hub_dir() / "nifty50" / "constituents_monthly_long.parquet"
    _write_parquet(wide, out_wide)
    _write_parquet(long_panel, out_long)

    current = ingest_nifty50_constituents_current(force_fetch=force_fetch)

    return {
        "status": "ok",
        "source": "https://github.com/vishalvx/nifty-indices-datasets",
        "note": "yfiua/index-constituents does not include Nifty 50 — use vishalvx for India survivorship-free panel",
        "months": len(wide),
        "symbols_tracked": len(symbol_cols),
        "membership_rows": len(long_panel),
        "start": str(wide["date"].iloc[0]),
        "end": str(wide["date"].iloc[-1]),
        "current_constituents": current,
        "merged_into_macro_daily": False,
    }


def _current_constituents_from_vishalvx() -> dict[str, Any] | None:
    """Derive latest Nifty 50 membership from vishalvx monthly weights panel."""
    wide_path = hub_dir() / "nifty50" / "constituents_monthly_wide.parquet"
    wide = _read_parquet(wide_path)
    if wide.empty or "date" not in wide.columns:
        cache = hub_dir() / "cache" / "vishalvx" / "nifty50_weights.csv"
        if not cache.is_file():
            return None
        wide = pd.read_csv(cache)
        wide = wide.rename(columns={"DATE": "date"})
        wide["date"] = pd.to_datetime(wide["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    latest = wide.sort_values("date").iloc[-1]
    day = str(latest["date"])
    symbols = [
        str(col).upper()
        for col in wide.columns
        if col != "date" and pd.notna(latest.get(col)) and float(latest[col]) > 0
    ]
    if not symbols:
        return None

    payload = {
        "as_of": day,
        "source": "vishalvx_nifty_indices_fallback",
        "symbols": symbols,
        "count": len(symbols),
    }
    out = hub_dir() / "nifty50" / "constituents_current.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def ingest_nifty50_constituents_current(*, force_fetch: bool = False) -> dict[str, Any]:
    """Latest Nifty 50 list — local CSV first, then NSE Indices official CSV."""
    local = ingest_nifty50_constituents_local()
    if local.get("status") == "ok":
        return local

    cache = hub_dir() / "cache" / "niftyindices"
    dest = cache / "ind_nifty50list.csv"
    url = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
    if force_fetch or not dest.is_file():
        try:
            fetch_to_path(url, dest, force=force_fetch, timeout=20, max_retries=2)
        except Exception as exc:
            if dest.is_file():
                logger.warning("niftyindices fetch failed, using cache: %s", exc)
            else:
                fallback = _current_constituents_from_vishalvx()
                if fallback:
                    logger.info("niftyindices unreachable — using vishalvx fallback (%s symbols)", fallback.get("count"))
                    return fallback
                return {"status": "skipped", "reason": "niftyindices_unreachable", "error": str(exc)}
    elif dest.is_file():
        logger.debug("Using cached niftyindices constituents CSV")

    if not dest.is_file():
        fallback = _current_constituents_from_vishalvx()
        if fallback:
            return fallback
        return {"status": "skipped", "reason": "no_constituents_csv"}

    frame = pd.read_csv(dest)
    frame.columns = [str(c).strip() for c in frame.columns]
    sym_col = next((c for c in frame.columns if c.lower() == "symbol"), frame.columns[2] if len(frame.columns) > 2 else "Symbol")
    symbols = frame[sym_col].astype(str).str.strip().str.upper().tolist()
    payload = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source": "niftyindices_official",
        "symbols": symbols,
        "count": len(symbols),
    }
    out = hub_dir() / "nifty50" / "constituents_current.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def ingest_forex_factory_calendar(*, force_fetch: bool = False) -> dict[str, Any]:
    """Global economic calendar (2007–2025) from Hugging Face Forex Factory dataset."""
    cache = hub_dir() / "cache" / "forex_factory"
    dest = cache / "forex_factory_cache.csv"
    url = "https://huggingface.co/datasets/Ehsanrs2/Forex_Factory_Calendar/resolve/main/forex_factory_cache.csv"
    _fetch_raw(url, dest, force=force_fetch)

    frame = pd.read_csv(dest)
    frame.columns = [str(c).strip() for c in frame.columns]
    frame["event_time"] = pd.to_datetime(frame.get("DateTime"), errors="coerce", utc=True)
    frame["date"] = frame["event_time"].dt.strftime("%Y-%m-%d")
    frame["impact"] = frame.get("Impact", "").astype(str)
    frame["currency"] = frame.get("Currency", "").astype(str).str.upper()
    frame["event"] = frame.get("Event", "").astype(str)
    frame["source"] = "huggingface_forex_factory"

    out = hub_dir() / "events" / "forex_factory_calendar.parquet"
    _write_parquet(frame, out)

    high_us = frame[
        frame["currency"].isin(["USD", "US", "USA"])
        & frame["impact"].str.contains("High", case=False, na=False)
    ].copy()
    daily_counts = (
        high_us.groupby("date", as_index=False)
        .size()
        .rename(columns={"size": "us_high_impact_events"})
        .sort_values("date")
    )
    daily_counts["source"] = "huggingface_forex_factory"
    events_cold = save_history_dataset("global_economic_events_daily", daily_counts)

    return {
        "status": "ok",
        "source": "https://huggingface.co/datasets/Ehsanrs2/Forex_Factory_Calendar",
        "events_rows": len(frame),
        "us_high_impact_days": len(daily_counts),
        "start": str(frame["date"].min()) if not frame.empty else None,
        "end": str(frame["date"].max()) if not frame.empty else None,
        "cold_tier": events_cold,
        "merged_into_macro_daily": False,
        "note": "Event-level panel in hub; daily US high-impact count in global_economic_events_daily",
    }


def _fetch_mrchartist_github_static(*, force_fetch: bool = False) -> dict[str, Any]:
    """Download MrChartist repo JSON archives (throttled raw GitHub fetches)."""
    base = "https://raw.githubusercontent.com/MrChartist/fii-dii-data/main"
    rel_paths = (
        "data/history.json",
        "data/latest.json",
        "data/fpi_daily.json",
        "data/fpi_monthly_history.json",
        "data/fpi_quarterly.json",
        "data/fpi_yearly_monthly.json",
        "data/sector_history.json",
        "data/sector_latest.json",
        "data/sectors.json",
        "data/debt_utilisation.json",
        "data/country_auc.json",
        "data/odi_pn.json",
        "data/fetch-log.json",
    )
    cache = hub_dir() / "cache" / "mrchartist_github"
    cache.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    errors: list[str] = []

    for rel in rel_paths:
        dest = cache / rel.replace("/", "_")
        try:
            fetch_to_path(f"{base}/{rel}", dest, force=force_fetch, timeout=120)
            saved.append(rel)
        except Exception as exc:
            errors.append(f"{rel}: {exc}")
            logger.warning("MrChartist GitHub file skipped %s: %s", rel, exc)

    return {"saved": saved, "errors": errors, "cache_dir": str(cache)}


def _frame_from_mrchartist_history(payload: list[dict[str, Any]], *, include_seeded: bool = False) -> pd.DataFrame:
    from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
        _float_or_none,
        _is_seeded_row,
        _parse_api_date,
    )

    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if not include_seeded and _is_seeded_row(item):
            continue
        day = _parse_api_date(str(item.get("date") or item.get("d") or ""))
        if not day:
            continue
        row: dict[str, Any] = {"date": day, "source": "mrchartist_github"}
        for src, dest in (
            ("fii_net", "fii_net"),
            ("dii_net", "dii_net"),
            ("fii_buy", "fii_buy"),
            ("fii_sell", "fii_sell"),
            ("dii_buy", "dii_buy"),
            ("dii_sell", "dii_sell"),
            ("pcr", "nifty_pcr"),
            ("sentiment_score", "fii_sentiment_score"),
            ("fii_idx_fut_long", "fii_idx_fut_long"),
            ("fii_idx_fut_short", "fii_idx_fut_short"),
            ("fii_idx_put_short", "fii_idx_put_oi"),
            ("fii_idx_call_short", "fii_idx_call_oi"),
        ):
            val = _float_or_none(item.get(src))
            if val is not None:
                row[dest] = val
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")
    long_oi = frame.get("fii_idx_fut_long")
    short_oi = frame.get("fii_idx_fut_short")
    if long_oi is not None and short_oi is not None:
        frame["fii_fut_long_short_ratio"] = long_oi / short_oi.replace(0, pd.NA)
    return frame


def ingest_mrchartist_hub_archive(*, allow_live_fetch: bool = True, force_fetch: bool = False) -> dict[str, Any]:
    """Persist MrChartist FII/DII archive to hub and merge into flow cold tier."""
    from trade_integrations.dataflows.index_research.history_ingest import merge_with_priority
    from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
        fetch_mrchartist_flow_frame,
    )

    github_meta = _fetch_mrchartist_github_static(force_fetch=force_fetch)
    github_frame = pd.DataFrame()
    github_raw_rows = 0
    history_path = hub_dir() / "cache" / "mrchartist_github" / "data_history.json"
    if history_path.is_file():
        try:
            payload = json.loads(history_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                github_raw_rows = len(payload)
                github_frame = _frame_from_mrchartist_history(payload)
                raw_out = hub_dir() / "flows" / "mrchartist_github_history.parquet"
                _write_parquet(pd.DataFrame(payload), raw_out)
        except Exception as exc:
            logger.warning("MrChartist GitHub history parse failed: %s", exc)

    api_frame = pd.DataFrame()
    if allow_live_fetch:
        api_frame = fetch_mrchartist_flow_frame(include_seeded=False, allow_live_fetch=True)
        if not api_frame.empty:
            api_frame = api_frame.copy()
            api_frame["source"] = "mrchartist_api"

    if not api_frame.empty and len(api_frame) >= len(github_frame):
        frame = api_frame
        flow_source = "mrchartist_api"
    elif not github_frame.empty:
        frame = github_frame
        flow_source = "mrchartist_github"
    else:
        frame = pd.DataFrame()
        flow_source = "none"

    if frame.empty:
        return {
            "status": "skipped",
            "reason": "empty_mrchartist_frame",
            "source": "https://github.com/MrChartist/fii-dii-data",
            "github_static": github_meta,
        }

    out = hub_dir() / "flows" / "mrchartist_daily.parquet"
    _write_parquet(frame, out)

    archive_path = hub_dir() / "flows" / "mrchartist_history.json"
    archive_path.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "rows": len(frame),
                "github_raw_rows": github_raw_rows,
                "source": flow_source,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    existing = load_history_dataset("flow_cash_daily")
    merged = merge_with_priority([existing, frame], on=["date"]) if not existing.empty else frame
    cold = save_history_dataset("flow_cash_daily", merged) if not merged.empty else {"status": "skipped"}

    return {
        "status": "ok",
        "source": "https://github.com/MrChartist/fii-dii-data",
        "api": "https://fii-diidata.mrchartist.com/api/history-full",
        "rows": len(frame),
        "flow_source": flow_source,
        "github_raw_rows": github_raw_rows,
        "github_static": github_meta,
        "cold_tier": cold,
        "merged_into_flow_cash_daily": cold.get("status") == "ok",
        "merge_policy": "mrchartist rank 60 — merged with NSE/niftyinvest sources on date",
    }


def ingest_nifty_technicals_panel() -> dict[str, Any]:
    """Compute moving averages/RSI/MACD from Nifty OHLCV (pandas-free; uses existing technical_features)."""
    from trade_integrations.dataflows.index_research.technical_features import enrich_nifty_technical_columns

    ohlcv = load_history_dataset("nifty_ohlcv_daily")
    if ohlcv.empty or "close" not in ohlcv.columns:
        return {"status": "skipped", "reason": "missing_nifty_ohlcv_daily"}

    base = ohlcv[["date", "close"]].copy()
    technical = enrich_nifty_technical_columns(base)
    out = hub_dir() / "nifty50" / "technicals_daily.parquet"
    _write_parquet(technical, out)

    macro = load_history_dataset("macro_daily")
    macro_result: dict[str, Any] = {"status": "skipped"}
    if not macro.empty and not technical.empty:
        tech_cols = [c for c in technical.columns if c not in {"date", "close"}]
        merged = macro.merge(technical[["date", *tech_cols]], on="date", how="left", suffixes=("", "_new"))
        for col in tech_cols:
            new_col = f"{col}_new"
            if new_col in merged.columns:
                if col in merged.columns:
                    merged[col] = merged[col].combine_first(merged[new_col])
                else:
                    merged[col] = merged[new_col]
                merged = merged.drop(columns=[new_col])
        macro_result = save_history_dataset("macro_daily", merged)

    return {
        "status": "ok",
        "rows": len(technical),
        "columns": [c for c in technical.columns if c != "date"],
        "macro_daily": macro_result,
        "merged_into_macro_daily": macro_result.get("status") == "ok",
        "note": "Computed from nifty_ohlcv_daily — equivalent to pandas-ta SMA/RSI/MACD pipeline",
    }


def ingest_curated_market_data(
    *,
    force_fetch: bool = False,
    include_kaggle: bool = True,
    allow_live_fetch: bool = True,
) -> dict[str, Any]:
    """Run all curated market ingest passes."""
    logger.info("Curated ingest starting (fetch delay=%.1fs)", fetch_delay_sec())
    results: dict[str, Any] = {
        "nifty50_valuation_github": ingest_nifty50_valuation_github(force_fetch=force_fetch),
        "nifty50_valuation_local": ingest_nifty50_valuation_local(),
        "nifty50_constituents_local": ingest_nifty50_constituents_local(),
        "nifty50_constituents": ingest_nifty50_constituents_historical(force_fetch=force_fetch),
        "forex_factory": ingest_forex_factory_calendar(force_fetch=force_fetch),
        "mrchartist": ingest_mrchartist_hub_archive(allow_live_fetch=allow_live_fetch, force_fetch=force_fetch),
        "nifty_technicals": ingest_nifty_technicals_panel(),
        "primeinvestor": {
            "status": "skipped",
            "reason": "no_public_csv_api",
            "alternative": "https://www.niftyindices.com/reports/historical-data",
            "note": "PrimeInvestor returns are presentation-layer; NSE Indices + RSwarnkar/GitHub cover PE/PB/price",
        },
        "stockedge_fii_dii": {
            "status": "skipped",
            "reason": "no_bulk_download",
            "alternative": "MrChartist + NSE repo flows already ingested",
        },
        "tapetide_mcp": {
            "status": "live_enrichment_only",
            "note": "Tapetide MCP wired in company_research when TAPETIDE_TOKEN set — not bulk-ingested",
        },
        "yfiua_index_constituents": {
            "status": "skipped",
            "reason": "no_nifty50_index",
            "note": "yfiua covers SP500/NASDAQ/HSI only — India Nifty 50 uses vishalvx/nifty-indices-datasets",
            "url": "https://github.com/yfiua/index-constituents",
        },
    }

    if include_kaggle:
        results["nifty50_valuation_kaggle"] = ingest_nifty50_valuation_kaggle()

    manifest = {
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "hub_dir": str(hub_dir()),
        "results": results,
    }
    hub_dir().mkdir(parents=True, exist_ok=True)
    (hub_dir() / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return {"status": "ok", "results": results, "manifest": manifest}


def verify_curated_market_data() -> dict[str, Any]:
    """Verify curated datasets and merge flags."""
    report: dict[str, Any] = {"hub_dir": str(hub_dir())}
    manifest_path = hub_dir() / "manifest.json"
    if manifest_path.is_file():
        report["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))

    val = _read_parquet(hub_dir() / "nifty50" / "valuation_daily.parquet")
    report["nifty50_valuation"] = {
        "rows": len(val),
        "has_pe": "nifty_pe" in val.columns and val["nifty_pe"].notna().any() if not val.empty else False,
        "merged_pe_in_macro": "nifty_pe" in load_history_dataset("macro_daily").columns,
    }
    local_val_path = hub_dir() / "nifty50" / "valuation_daily.parquet"
    report["nifty50_valuation_local"] = {
        "ingested": bool(
            not val.empty
            and "source" in val.columns
            and val["source"].astype(str).str.contains("nse_historic", na=False).any()
        ),
        "rows": int(val["source"].astype(str).str.contains("nse_historic", na=False).sum())
        if not val.empty and "source" in val.columns
        else 0,
        "path": str(local_val_path),
    }

    cons_current = hub_dir() / "nifty50" / "constituents_current.json"
    if cons_current.is_file():
        report["nifty50_constituents_current"] = json.loads(cons_current.read_text(encoding="utf-8"))

    cons = _read_parquet(hub_dir() / "nifty50" / "constituents_monthly_long.parquet")
    report["nifty50_constituents_historical"] = {
        "membership_rows": len(cons),
        "symbols": int(cons["symbol"].nunique()) if not cons.empty and "symbol" in cons.columns else 0,
        "merged_into_macro_daily": False,
    }

    events = _read_parquet(hub_dir() / "events" / "forex_factory_calendar.parquet")
    report["forex_factory"] = {"events": len(events), "merged_into_macro_daily": False}

    flows = load_history_dataset("flow_cash_daily")
    report["flow_cash_daily"] = {
        "rows": len(flows),
        "mrchartist_rows": int((flows.get("source", pd.Series(dtype=str)) == "mrchartist").sum()) if not flows.empty and "source" in flows.columns else 0,
    }
    return report
