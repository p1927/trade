"""Parse user-curated files under data/nse/historic_data/."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.pipeline_cancel import check_pipeline_cancel
from trade_integrations.hub_storage.date_parse import (
    format_date_series,
    format_datetime_series,
    parse_date_scalar,
    parse_date_series,
)
from trade_integrations.hub_storage.parquet_io import (
    combine_first_numeric,
    concat_dataframes,
    concat_frames,
)

from trade_integrations.nse_browser.parsers.structural_adjustments import (
    apply_symbol_succession_to_weights_wide,
    enrich_adjusted_constituent_prices,
    rebuild_weights_long,
)

logger = logging.getLogger(__name__)

_HISTORIC_DIR = "historic_data"
_INDIA_STOCK_MARKET_XLSX = "India_Stock_Market_Data.xlsx"
_LOCAL_NIFTY50_VALUATION_NAMES = (
    "nifty50_historical_pe_pb_div.csv",
    "NIFTY 50_Historical_PE_PB_DIV.csv",
    "Nifty50_Historical_PE_PB_DIV.csv",
)
_LOCAL_NIFTY50_LIST_NAMES = ("ind_nifty50list.csv",)
_ARCHIVE_DIR = "archive"
_FIGSHARE_DIR = "dataset_figshare"
_CONSTITUENTS_NIFTY50_DIR = "contituents nifty 50"
_INTRADAY_DIR = "archive (4)"
_EQUITY_PANEL_DIR = "archive (7)"
_GLOBAL_MACRO_DIR = "global-india-markets-macro"
_AMP4010_DIR = "amp4010"
_RBI_DIR = "rbi"
_NIFTYINDICES_DIR = "niftyindices"
_MRCHARTIST_HISTORY_JSON = "mrchartist_history_full.json"
_INDIC_FINANCE_CSV = "indic-finance.csv"
_HANDLED_SUBDIRS = frozenset(
    {
        _ARCHIVE_DIR,
        _FIGSHARE_DIR,
        _CONSTITUENTS_NIFTY50_DIR,
        _INTRADAY_DIR,
        _EQUITY_PANEL_DIR,
        _GLOBAL_MACRO_DIR,
        _AMP4010_DIR,
        _RBI_DIR,
        _NIFTYINDICES_DIR,
    }
)

_NIFTY50_CONSTITUENTS_SOURCES: tuple[tuple[str, str], ...] = (
    (_FIGSHARE_DIR, "historic_data_figshare"),
    (_CONSTITUENTS_NIFTY50_DIR, "historic_data_constituents_nifty50"),
    (_AMP4010_DIR, "historic_data_amp4010"),
)

_INDEX_OHLCV_FILES: dict[str, str] = {
    "NIFTY_50.csv": "nifty50",
    "SENSEX.csv": "sensex",
}
_CONSTITUENT_OHLCV_FILES: dict[str, str] = {
    "NIFTY_50_COMPANIES.csv": "nifty50",
    "SENSEX_COMPANIES.csv": "sensex",
}

_OHLCV_RENAME: dict[str, str] = {
    "Date": "date",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
    "Daily_Return_%": "daily_return_pct",
    "SMA_20": "sma_20",
    "SMA_50": "sma_50",
    "EMA_12": "ema_12",
    "EMA_26": "ema_26",
    "MACD": "macd",
    "Signal_Line": "signal_line",
    "RSI_14": "rsi_14",
    "BB_Mid": "bb_mid",
    "BB_Upper": "bb_upper",
    "BB_Lower": "bb_lower",
}

_COLUMN_MAP: dict[str, str] = {
    "Year": "year",
    "GDP Growth (%)": "gdp_growth_pct",
    "SENSEX": "sensex_close",
    "Inflation (%)": "inflation_pct",
    "Exch Rate (INR/USD)": "usd_inr",
    "SENSEX_Ret (%)": "sensex_return_pct",
    "Mod_Infl_Dummy (4-6%)": "inflation_mod_dummy",
    "High_Infl_Dummy (>6%)": "inflation_high_dummy",
}


def historic_data_dir(repo_root: Path) -> Path:
    return repo_root / _HISTORIC_DIR


def _write_dataset(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_parquet(path, index=False)
    except ImportError:
        frame.to_csv(path.with_suffix(".csv"), index=False)
    frame.to_csv(path.with_suffix(".csv"), index=False)


def _historic_manifest_path(repo_root: Path) -> Path:
    return historic_data_dir(repo_root) / "manifest.json"


def _load_historic_manifest(repo_root: Path) -> dict[str, Any]:
    path = _historic_manifest_path(repo_root)
    if not path.is_file():
        return {"datasets": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"datasets": {}}
    if not isinstance(payload, dict):
        return {"datasets": {}}
    payload.setdefault("datasets", {})
    return payload


def _save_historic_manifest(repo_root: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = _historic_manifest_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _source_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "source_path": path.name,
        "source_mtime": int(stat.st_mtime_ns),
        "source_sha256": digest.hexdigest(),
    }


def _dataset_output_exists(out_path: Path) -> bool:
    return out_path.is_file() or out_path.with_suffix(".csv").is_file()


def _historic_parser_version() -> str:
    """Fingerprint parser logic so manifest skip busts after code changes."""
    digest = hashlib.sha256()
    for module_path in (
        Path(__file__).resolve(),
        Path(__file__).resolve().parents[2] / "hub_storage" / "date_parse.py",
    ):
        if module_path.is_file():
            digest.update(module_path.read_bytes())
    return digest.hexdigest()


def _should_skip_historic_dataset(
    *,
    manifest: dict[str, Any],
    dataset_key: str,
    source_path: Path,
    out_path: Path,
) -> bool:
    if not source_path.is_file() or not _dataset_output_exists(out_path):
        return False
    prev = (manifest.get("datasets") or {}).get(dataset_key)
    if not isinstance(prev, dict):
        return False
    current = _source_fingerprint(source_path)
    parser_version = _historic_parser_version()
    if prev.get("parser_version") != parser_version:
        return False
    return prev.get("source_sha256") == current["source_sha256"]


def _record_historic_dataset(
    manifest: dict[str, Any],
    *,
    dataset_key: str,
    source_path: Path,
    out_path: Path,
    meta: dict[str, Any],
) -> None:
    datasets = manifest.setdefault("datasets", {})
    entry = {
        **_source_fingerprint(source_path),
        "path": str(out_path),
        "parser_version": _historic_parser_version(),
        **meta,
    }
    datasets[dataset_key] = entry


def _write_dataset_if_changed(
    repo_root: Path,
    manifest: dict[str, Any],
    *,
    dataset_key: str,
    source_path: Path,
    frame: pd.DataFrame,
    out_path: Path,
    meta: dict[str, Any],
) -> bool:
    """Write dataset when source changed or output missing. Returns True if written."""
    if frame.empty:
        return False
    if _should_skip_historic_dataset(
        manifest=manifest,
        dataset_key=dataset_key,
        source_path=source_path,
        out_path=out_path,
    ):
        logger.debug("skipping unchanged historic dataset %s (%s)", dataset_key, source_path.name)
        return False
    _write_dataset(frame, out_path)
    _record_historic_dataset(
        manifest,
        dataset_key=dataset_key,
        source_path=source_path,
        out_path=out_path,
        meta=meta,
    )
    return True


def _record_skipped_historic_dataset(
    results: dict[str, Any],
    manifest: dict[str, Any],
    *,
    dataset_key: str,
    out_path: Path,
    extra: dict[str, Any] | None = None,
) -> None:
    prev = (manifest.get("datasets") or {}).get(dataset_key) or {}
    payload: dict[str, Any] = {
        "rows": int(prev.get("rows") or prev.get("symbols") or 0),
        "path": str(out_path),
        "skipped": True,
        "start": prev.get("start"),
        "end": prev.get("end"),
    }
    if extra:
        payload.update(extra)
    results["datasets"][dataset_key] = payload


def _ingest_frame_with_manifest(
    repo_root: Path,
    manifest: dict[str, Any],
    results: dict[str, Any],
    *,
    dataset_key: str,
    source_path: Path,
    frame: pd.DataFrame,
    meta: dict[str, Any],
) -> None:
    if frame.empty:
        return
    out_path = _dataset_path(repo_root, dataset_key)
    if _write_dataset_if_changed(
        repo_root,
        manifest,
        dataset_key=dataset_key,
        source_path=source_path,
        frame=frame,
        out_path=out_path,
        meta=meta,
    ):
        results["datasets"][dataset_key] = {"path": str(out_path), **meta}
    elif _should_skip_historic_dataset(
        manifest=manifest,
        dataset_key=dataset_key,
        source_path=source_path,
        out_path=out_path,
    ):
        _record_skipped_historic_dataset(
            results,
            manifest,
            dataset_key=dataset_key,
            out_path=out_path,
            extra=meta,
        )


def _read_dataset(path: Path) -> pd.DataFrame:
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


def _normalize_symbol(raw: object) -> str:
    symbol = str(raw or "").strip().upper()
    if symbol.endswith(".NS"):
        symbol = symbol[:-3]
    return symbol


def scan_historic_data_dir(repo_root: Path) -> list[Path]:
    """List ingestible files in historic_data/ (top level and archive/figshare subdirs)."""
    root = historic_data_dir(repo_root)
    if not root.is_dir():
        return []
    paths: list[Path] = []
    for pattern in ("*.xlsx", "*.xls", "*.csv"):
        paths.extend(sorted(root.glob(pattern)))
        for subdir in _HANDLED_SUBDIRS:
            paths.extend(sorted((root / subdir).glob(pattern)))
    return paths


def parse_india_stock_market_xlsx(path: Path) -> pd.DataFrame:
    """Parse India_Stock_Market_Data.xlsx into normalized annual macro rows."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_excel(path, sheet_name=0)
    except Exception as exc:
        logger.warning("historic_data xlsx read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    rename = {col: _COLUMN_MAP[col] for col in raw.columns if col in _COLUMN_MAP}
    frame = raw.rename(columns=rename)
    if "year" not in frame.columns:
        return pd.DataFrame()

    out = frame.copy()
    out["year"] = pd.to_numeric(out["year"], errors="coerce").astype("Int64")
    out = out.dropna(subset=["year"])
    out["year"] = out["year"].astype(int)

    for col in (
        "gdp_growth_pct",
        "sensex_close",
        "inflation_pct",
        "usd_inr",
        "sensex_return_pct",
        "inflation_mod_dummy",
        "inflation_high_dummy",
    ):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "sensex_close" in out.columns:
        out.loc[out["sensex_close"] <= 0, "sensex_close"] = pd.NA

    out["date"] = out["year"].astype(str) + "-12-31"
    out["granularity"] = "annual"
    out["source"] = "historic_data_xlsx"
    out["source_file"] = path.name

    value_cols = [c for c in out.columns if c not in {"year", "date", "granularity", "source", "source_file"}]
    out = out[["year", "date", "granularity", "source", "source_file", *value_cols]]
    return out.sort_values("year").drop_duplicates("year", keep="last").reset_index(drop=True)


def local_nifty50_valuation_csv_path(repo_root: Path) -> Path | None:
    root = historic_data_dir(repo_root)
    for name in _LOCAL_NIFTY50_VALUATION_NAMES:
        path = root / name
        if path.is_file():
            return path
    return None


def local_ind_nifty50_list_csv_path(repo_root: Path) -> Path | None:
    root = historic_data_dir(repo_root)
    for name in _LOCAL_NIFTY50_LIST_NAMES:
        path = root / name
        if path.is_file():
            return path
    return None


def parse_nifty50_pe_pb_div_csv(path: Path) -> pd.DataFrame:
    """Parse Nifty Indices historical PE/PB/Div CSV (local drop or Kaggle export)."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("nifty50 pe/pb/div read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    cols = {str(c).strip(): c for c in raw.columns}
    rename = {}
    for src, dst in (
        ("Date", "date"),
        ("Close", "nifty_close"),
        ("EPS", "nifty_eps"),
        ("P/E", "nifty_pe"),
        ("P/B", "nifty_pb"),
        ("Div Yield %", "nifty_dividend_yield"),
    ):
        if src in cols:
            rename[cols[src]] = dst
    out = raw.rename(columns=rename)
    if "date" not in out.columns:
        return pd.DataFrame()

    parsed = parse_date_series(out["date"])
    out["date"] = parsed.dt.strftime("%Y-%m-%d")
    out = out[out["date"].notna()].copy()

    for col in ("nifty_close", "nifty_eps", "nifty_pe", "nifty_pb", "nifty_dividend_yield"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    out["source"] = "nse_historic_data_pe_pb_div"
    out["source_file"] = path.name
    keep = [c for c in ("date", "nifty_pe", "nifty_pb", "nifty_dividend_yield", "nifty_close", "nifty_eps", "source", "source_file") if c in out.columns]
    return out[keep].drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def _is_nifty50_valuation_csv(path: Path) -> bool:
    name = path.name
    if name in _LOCAL_NIFTY50_VALUATION_NAMES:
        return True
    upper = name.upper()
    return "NIFTY" in upper and "PE" in upper and "PB" in upper and "DIV" in upper and name.lower().endswith(".csv")


def _merge_nifty50_valuation_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    valid = [frame for frame in frames if frame is not None and not frame.empty]
    if not valid:
        return pd.DataFrame()
    merged = concat_frames(valid)
    merged["date"] = merged["date"].astype(str).str[:10]
    return merged.sort_values(["date", "source_file"]).drop_duplicates("date", keep="last").reset_index(drop=True)


def discover_nifty50_valuation_csvs(repo_root: Path) -> list[Path]:
    root = historic_data_dir(repo_root)
    if not root.is_dir():
        return []
    paths: list[Path] = []
    seen: set[str] = set()
    for name in _LOCAL_NIFTY50_VALUATION_NAMES:
        path = root / name
        if path.is_file():
            paths.append(path)
            seen.add(path.name)
    for path in sorted(root.glob("*.csv")):
        if path.name in seen:
            continue
        if _is_nifty50_valuation_csv(path):
            paths.append(path)
            seen.add(path.name)
    return paths


def parse_ind_nifty50_list_csv(path: Path) -> dict[str, Any]:
    """Parse official Nifty 50 constituent list CSV."""
    if not path.is_file():
        return {"status": "skipped", "reason": "missing_file"}
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("ind_nifty50list read failed %s: %s", path.name, exc)
        return {"status": "error", "error": str(exc)}

    raw.columns = [str(c).strip() for c in raw.columns]
    sym_col = next((c for c in raw.columns if c.lower() == "symbol"), None)
    if sym_col is None:
        return {"status": "error", "reason": "missing_symbol_column"}

    symbols = raw[sym_col].astype(str).str.strip().tolist()
    symbols = [s for s in symbols if s and s.lower() != "symbol"]
    return {
        "status": "ok",
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "source": "nse_historic_data_ind_nifty50list",
        "source_file": path.name,
        "symbols": symbols,
        "count": len(symbols),
    }


def parse_fii_dii_trading_activity_csv(path: Path) -> pd.DataFrame:
    """Parse annual FII/DII trading activity CSV."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("FII/DII activity read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    cols = {str(c).strip().lower(): c for c in raw.columns}
    date_col = cols.get("date")
    if date_col is None:
        return pd.DataFrame()
    out = pd.DataFrame({"date": format_date_series(raw[date_col], dayfirst=True)})
    mapping = {
        "fii_gross_purchase": "fii_buy",
        "fii_gross_sales": "fii_sell",
        "fii_net_purchase/sales": "fii_net",
        "dii_gross_purchase": "dii_buy",
        "dii_gross_sales": "dii_sell",
        "dii_net_purchase/sales": "dii_net",
    }
    for src_key, dest in mapping.items():
        src = cols.get(src_key)
        if src:
            out[dest] = pd.to_numeric(raw[src], errors="coerce")
    out = out.dropna(subset=["date"])
    out["source"] = "historic_data_fii_dii_activity"
    out["source_file"] = path.name
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def parse_india_cpi_monthly_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("india CPI read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if "date" not in raw.columns:
        return pd.DataFrame()
    out = raw.copy()
    out["date"] = format_date_series(out["date"])
    out = out.dropna(subset=["date"]).sort_values("date")
    out["source"] = "historic_data_india_cpi"
    out["source_file"] = path.name
    return out.drop_duplicates("date", keep="last").reset_index(drop=True)


def parse_nifty_fo_oi_daily_csv(path: Path) -> pd.DataFrame:
    """Aggregate intraday Nifty OI ticks to daily mean PCR/OI."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("nifty OI read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    cols = {str(c).strip().lower(): c for c in raw.columns}
    date_col = cols.get("date")
    if date_col is None:
        return pd.DataFrame()
    raw_dates = raw[date_col].astype(str).str.strip()
    intraday_mask = raw_dates.str.match(r"^\d{2}_\d{2}_\d{2}$", na=False)
    parsed_dates = pd.Series(pd.NaT, index=raw_dates.index, dtype="datetime64[ns]")
    if intraday_mask.any():
        parsed_dates.loc[intraday_mask] = pd.to_datetime(
            raw_dates.loc[intraday_mask],
            format="%d_%m_%y",
            errors="coerce",
        )
    iso_mask = ~intraday_mask
    if iso_mask.any():
        iso_values = raw_dates.loc[iso_mask].str.replace("_", "-", regex=False)
        parsed_dates.loc[iso_mask] = parse_date_series(iso_values, dayfirst=True)
    work = pd.DataFrame({"date": parsed_dates.dt.strftime("%Y-%m-%d")})
    for src, dest in (("calloi", "call_oi"), ("putoi", "put_oi"), ("pcr", "pcr"), ("niftyspot", "nifty_spot")):
        col = cols.get(src)
        if col:
            work[dest] = pd.to_numeric(raw[col], errors="coerce")
    work = work.dropna(subset=["date"])
    daily = (
        work.groupby("date", as_index=False)
        .agg(
            call_oi=("call_oi", "mean"),
            put_oi=("put_oi", "mean"),
            pcr=("pcr", "mean"),
            nifty_spot=("nifty_spot", "mean"),
            ticks=("date", "count"),
        )
        .sort_values("date")
    )
    daily["source"] = "historic_data_nifty_oi"
    daily["source_file"] = path.name
    return daily.reset_index(drop=True)


def parse_nifty50_fo_panel_csv(path: Path, *, max_rows: int = 500_000) -> pd.DataFrame:
    """Parse NSE FO filtered panel (large — capped rows for cold-tier storage)."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig", nrows=max_rows)
    except Exception as exc:
        logger.warning("nifty50 FO panel read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()
    out = raw.copy()
    if "TIMESTAMP" in out.columns:
        out["date"] = format_date_series(out["TIMESTAMP"])
    out["source"] = "historic_data_nifty50_fo"
    out["source_file"] = path.name
    return out


def _parse_historic_csv(path: Path) -> pd.DataFrame:
    """Best-effort parse for generic CSV drops with a date column."""
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()
    cols = {str(c).strip().lower(): c for c in raw.columns}
    date_col = cols.get("date")
    if date_col is None:
        return pd.DataFrame()
    out = raw.copy()
    out["date"] = format_date_series(out[date_col])
    out = out.dropna(subset=["date"])
    if out.empty:
        return pd.DataFrame()
    out["source"] = "historic_data_csv"
    out["source_file"] = path.name
    out["granularity"] = "daily"
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def parse_niftyindices_price_csv(path: Path, *, index_slug: str = "nifty50") -> pd.DataFrame:
    """Parse Nifty Indices historical price CSV (Index Name, Date, OHLC)."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("niftyindices price read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    cols = {str(c).strip().lower(): c for c in raw.columns}
    date_col = cols.get("date")
    if date_col is None:
        return pd.DataFrame()

    rename: dict[str, str] = {date_col: "date"}
    for src, dst in (
        ("open", "open"),
        ("high", "high"),
        ("low", "low"),
        ("close", "close"),
    ):
        if src in cols:
            rename[cols[src]] = dst

    out = raw.rename(columns=rename)
    out["date"] = format_date_series(out["date"], dayfirst=True)
    out = out.dropna(subset=["date"])
    if out.empty:
        return pd.DataFrame()

    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    out["index_slug"] = index_slug
    out["granularity"] = "daily"
    out["source"] = "historic_data_niftyindices"
    out["source_file"] = path.name
    keep = [c for c in ("date", "open", "high", "low", "close", "index_slug", "granularity", "source", "source_file") if c in out.columns]
    return out[keep].sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def parse_mrchartist_history_json(path: Path) -> pd.DataFrame:
    """Parse MrChartist history-full JSON (FII/DII cash + F&O positioning)."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("mrchartist history json read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if not isinstance(payload, list) or not payload:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        raw_date = str(item.get("d") or item.get("date") or "").strip()
        iso_date = parse_date_scalar(raw_date)
        if not iso_date:
            continue
        row: dict[str, Any] = {
            "date": iso_date,
            "source": "mrchartist_history_full",
            "source_file": path.name,
        }
        for src, dest in (
            ("fn", "fii_net"),
            ("fii_net", "fii_net"),
            ("dn", "dii_net"),
            ("dii_net", "dii_net"),
            ("fb", "fii_buy"),
            ("fs", "fii_sell"),
            ("db", "dii_buy"),
            ("ds", "dii_sell"),
            ("pcr", "nifty_pcr"),
            ("sentiment_score", "fii_sentiment_score"),
            ("fii_idx_fut_long", "fii_idx_fut_long"),
            ("fii_idx_fut_short", "fii_idx_fut_short"),
            ("fii_idx_opt_put_short", "fii_idx_put_oi"),
            ("fii_idx_opt_call_short", "fii_idx_call_oi"),
        ):
            if src not in item:
                continue
            try:
                val = float(item[src])
            except (TypeError, ValueError):
                continue
            if pd.notna(val):
                row[dest] = val
        if row.get("fii_net") is None and row.get("fii_buy") is not None and row.get("fii_sell") is not None:
            row["fii_net"] = row["fii_buy"] - row["fii_sell"]
        if row.get("dii_net") is None and row.get("dii_buy") is not None and row.get("dii_sell") is not None:
            row["dii_net"] = row["dii_buy"] - row["dii_sell"]
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")
    long_oi = frame.get("fii_idx_fut_long")
    short_oi = frame.get("fii_idx_fut_short")
    if long_oi is not None and short_oi is not None:
        frame["fii_fut_long_short_ratio"] = long_oi / short_oi.replace(0, pd.NA)
    if "fii_idx_put_oi" in frame.columns and "fii_idx_call_oi" in frame.columns:
        frame["nifty_pcr"] = combine_first_numeric(
            frame["nifty_pcr"],
            frame["fii_idx_put_oi"] / frame["fii_idx_call_oi"].replace(0, pd.NA),
        )
    return frame.reset_index(drop=True)


def parse_indic_finance_csv(path: Path) -> pd.DataFrame:
    """Parse Hugging Face indic-finance article-level sentiment export."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("indic-finance read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    cols = {str(c).strip().lower(): c for c in raw.columns}
    date_col = cols.get("parsed_date") or cols.get("date")
    if date_col is None:
        return pd.DataFrame()

    out = pd.DataFrame(
        {
            "date": format_date_series(raw[date_col]),
            "title": raw[cols["headline"]] if "headline" in cols else "",
            "symbol": raw[cols["ticker"]].map(_normalize_symbol) if "ticker" in cols else "",
            "url": raw[cols["url"]] if "url" in cols else "",
            "sentiment": raw[cols["sentiment_label"]].astype(str).str.lower() if "sentiment_label" in cols else "",
            "sentiment_positive": pd.to_numeric(raw[cols["sentiment_positive"]], errors="coerce")
            if "sentiment_positive" in cols
            else pd.NA,
            "sentiment_negative": pd.to_numeric(raw[cols["sentiment_negative"]], errors="coerce")
            if "sentiment_negative" in cols
            else pd.NA,
            "source": "historic_data_indic_finance",
            "source_file": path.name,
        }
    )
    return out.dropna(subset=["date"]).reset_index(drop=True)


def aggregate_indic_finance_daily(articles: pd.DataFrame) -> pd.DataFrame:
    """Roll indic-finance articles into daily sentiment aggregates."""
    if articles.empty:
        return pd.DataFrame()
    frame = articles.copy()
    pos = pd.to_numeric(frame.get("sentiment_positive"), errors="coerce")
    neg = pd.to_numeric(frame.get("sentiment_negative"), errors="coerce")
    frame["signed_score"] = pos.fillna(0) - neg.fillna(0)
    frame.loc[frame["sentiment"] == "negative", "signed_score"] = -neg.abs()
    frame.loc[frame["sentiment"] == "positive", "signed_score"] = pos.abs()
    grouped = frame.groupby("date", as_index=False).agg(
        sentiment_mean=("signed_score", "mean"),
        positive_count=("sentiment", lambda s: int((s == "positive").sum())),
        negative_count=("sentiment", lambda s: int((s == "negative").sum())),
        article_count=("sentiment", "count"),
    )
    grouped["source"] = "historic_data_indic_finance"
    return grouped.sort_values("date").reset_index(drop=True)


def parse_rbi_wss_ratios_csv(path: Path) -> pd.DataFrame:
    """Parse RBI WSS Table 5 weekly ratios (repo, yields, FX)."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("RBI WSS read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty or "date" not in raw.columns:
        return pd.DataFrame()

    out = raw.rename(columns={"date": "raw_date"}).copy()
    parsed_dates = parse_date_series(out["raw_date"])
    out["date"] = parsed_dates.dt.strftime("%Y-%m-%d")
    out = out.dropna(subset=["date"]).sort_values("date")

    def _first_float(series: pd.Series) -> pd.Series:
        extracted = series.astype(str).str.extract(r"([\d.]+)", expand=False)
        return pd.to_numeric(extracted, errors="coerce")

    rename_map = {
        "Policy Repo Rate": "repo_rate",
        "91-Day Treasury Bill (Primary) Yield": "india_91d_tbill",
        "10-Year G-Sec Par Yield (FBIL)": "india_10y",
        "INR-US$ Spot Rate ( Rs. Per Foreign Currency)": "usd_inr",
    }
    for src, dst in rename_map.items():
        if src in out.columns:
            out[dst] = _first_float(out[src])

    out["source"] = "historic_data_rbi_wss"
    out["source_file"] = path.name
    out["granularity"] = "weekly"
    keep = [c for c in ("date", "repo_rate", "india_91d_tbill", "india_10y", "usd_inr", "source", "source_file", "granularity") if c in out.columns]
    return out[keep].drop_duplicates("date", keep="last").reset_index(drop=True)


def _merge_index_ohlcv(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return incoming.copy()
    if incoming.empty:
        return existing.copy()
    merged = concat_dataframes(existing, incoming)
    merged["date"] = merged["date"].astype(str).str[:10]
    return merged.sort_values(["date", "source"]).drop_duplicates("date", keep="last").reset_index(drop=True)


def local_mrchartist_history_path(repo_root: Path) -> Path:
    return historic_data_dir(repo_root) / _MRCHARTIST_HISTORY_JSON


def parse_index_ohlcv_csv(path: Path, *, index_slug: str) -> pd.DataFrame:
    """Parse archive index OHLCV CSV (NIFTY_50.csv, SENSEX.csv)."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("index ohlcv read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    rename = {src: dst for src, dst in _OHLCV_RENAME.items() if src in raw.columns}
    out = raw.rename(columns=rename)
    if "date" not in out.columns:
        cols = {str(c).strip().lower(): c for c in raw.columns}
        if "date" in cols:
            out = out.rename(columns={cols["date"]: "date"})
    if "date" not in out.columns:
        return parse_niftyindices_price_csv(path, index_slug=index_slug)

    out["date"] = format_date_series(out["date"])
    out = out.dropna(subset=["date"])
    if out.empty:
        return pd.DataFrame()

    numeric_cols = [
        c
        for c in out.columns
        if c not in {"date", "source", "source_file", "index_slug", "granularity"}
    ]
    for col in numeric_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out["index_slug"] = index_slug
    out["granularity"] = "daily"
    out["source"] = "historic_data_archive"
    out["source_file"] = path.name
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def parse_constituent_ohlcv_csv(path: Path, *, index_slug: str) -> pd.DataFrame:
    """Parse archive constituent OHLCV CSV into long daily rows."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("constituent ohlcv read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty or "Ticker" not in raw.columns:
        return pd.DataFrame()

    rename = {src: dst for src, dst in _OHLCV_RENAME.items() if src in raw.columns}
    frame = raw.rename(columns=rename)
    if "date" not in frame.columns:
        return pd.DataFrame()

    frame["date"] = format_date_series(frame["date"])
    frame["symbol"] = frame["Ticker"].map(_normalize_symbol)
    frame = frame.dropna(subset=["date", "symbol"])
    if frame.empty:
        return pd.DataFrame()

    value_cols = [
        c
        for c in frame.columns
        if c
        not in {
            "date",
            "symbol",
            "Ticker",
            "source",
            "source_file",
            "index_slug",
            "granularity",
        }
    ]
    for col in value_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    out = frame[["date", "symbol", *value_cols]].copy()
    out["index_slug"] = index_slug
    out["granularity"] = "daily"
    out["source"] = "historic_data_archive"
    out["source_file"] = path.name
    out = enrich_adjusted_constituent_prices(out)
    return (
        out.sort_values(["date", "symbol"])
        .drop_duplicates(["date", "symbol"], keep="last")
        .reset_index(drop=True)
    )


def parse_figshare_weights_csv(
    path: Path,
    *,
    source: str = "historic_data_figshare",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse Nifty 50 monthly weights panel into wide + long frames."""
    if not path.is_file():
        return pd.DataFrame(), pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("nifty50 weights read failed %s: %s", path.name, exc)
        return pd.DataFrame(), pd.DataFrame()
    if raw.empty:
        return pd.DataFrame(), pd.DataFrame()

    date_col = "DATE" if "DATE" in raw.columns else "date"
    wide = raw.rename(columns={date_col: "date"}).copy()
    wide["date"] = format_date_series(wide["date"])
    wide = wide.dropna(subset=["date"]).sort_values("date")
    if wide.empty:
        return pd.DataFrame(), pd.DataFrame()

    symbol_cols = [c for c in wide.columns if c != "date"]
    wide = wide.rename(columns={col: str(col).upper() for col in symbol_cols})
    wide = apply_symbol_succession_to_weights_wide(wide)
    wide["source"] = source
    long_panel = rebuild_weights_long(wide, source=source)
    return wide.reset_index(drop=True), long_panel


def parse_figshare_sectors_csv(
    path: Path,
    *,
    source: str = "historic_data_figshare",
) -> pd.DataFrame:
    """Parse Figshare sector mapping (STOCK, SECTOR)."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("figshare sectors read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    cols = {str(c).strip().upper(): c for c in raw.columns}
    stock_col = cols.get("STOCK") or cols.get("SYMBOL") or cols.get("TICKER")
    sector_col = cols.get("SECTOR")
    if stock_col is None or sector_col is None:
        return pd.DataFrame()

    out = pd.DataFrame(
        {
            "symbol": raw[stock_col].map(_normalize_symbol),
            "sector": raw[sector_col].astype(str).str.strip(),
            "source": source,
        }
    )
    out = out[out["symbol"].astype(str).str.len() > 0]
    return out.drop_duplicates("symbol", keep="last").reset_index(drop=True)


def parse_nifty50_constituents_summary_csv(
    path: Path,
    *,
    source: str = "historic_data_constituents_nifty50",
) -> pd.DataFrame:
    """Parse summary.csv membership span per ticker (FIRST_DATE, LAST_DATE, NONZERO_COUNT)."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("nifty50 constituents summary read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    cols = {str(c).strip().upper(): c for c in raw.columns}
    ticker_col = cols.get("TICKER") or cols.get("SYMBOL") or cols.get("STOCK")
    first_col = cols.get("FIRST_DATE")
    last_col = cols.get("LAST_DATE")
    count_col = cols.get("NONZERO_COUNT")
    if ticker_col is None:
        return pd.DataFrame()

    out = pd.DataFrame(
        {
            "symbol": raw[ticker_col].map(_normalize_symbol),
            "first_date": format_date_series(raw[first_col])
            if first_col
            else pd.NA,
            "last_date": format_date_series(raw[last_col])
            if last_col
            else pd.NA,
            "nonzero_count": pd.to_numeric(raw[count_col], errors="coerce").astype("Int64")
            if count_col
            else pd.NA,
            "source": source,
            "source_file": path.name,
        }
    )
    out = out[out["symbol"].astype(str).str.len() > 0]
    out["date"] = out["last_date"].fillna(out["first_date"])
    return out.drop_duplicates("symbol", keep="last").reset_index(drop=True)


def _merge_nifty50_weights_panels(
    wide_frames: list[pd.DataFrame],
    long_frames: list[pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    valid_wide = [frame for frame in wide_frames if frame is not None and not frame.empty]
    valid_long = [frame for frame in long_frames if frame is not None and not frame.empty]
    if not valid_wide:
        return pd.DataFrame(), pd.DataFrame()
    wide = concat_frames(valid_wide)
    wide["date"] = wide["date"].astype(str).str[:10]
    wide = wide.sort_values(["date", "source"]).drop_duplicates("date", keep="last").reset_index(drop=True)
    if valid_long:
        long_panel = concat_frames(valid_long)
        long_panel["date"] = long_panel["date"].astype(str).str[:10]
        long_panel = (
            long_panel.sort_values(["date", "symbol", "source"])
            .drop_duplicates(["date", "symbol"], keep="last")
            .reset_index(drop=True)
        )
    else:
        long_panel = pd.DataFrame()
    return wide, long_panel


def _merge_nifty50_sector_maps(frames: list[pd.DataFrame]) -> pd.DataFrame:
    valid = [frame for frame in frames if frame is not None and not frame.empty]
    if not valid:
        return pd.DataFrame()
    combined = concat_frames(valid)
    return combined.sort_values(["symbol", "source"]).drop_duplicates("symbol", keep="last").reset_index(drop=True)


def parse_historic_data_file(path: Path) -> pd.DataFrame:
    """Route a single historic_data file to the appropriate parser."""
    name = path.name
    if name in _LOCAL_NIFTY50_VALUATION_NAMES:
        return parse_nifty50_pe_pb_div_csv(path)
    if name in _LOCAL_NIFTY50_LIST_NAMES:
        parsed = parse_ind_nifty50_list_csv(path)
        if parsed.get("status") != "ok":
            return pd.DataFrame()
        return pd.DataFrame({"symbol": parsed.get("symbols", []), "source": parsed.get("source")})
    if name == _INDIA_STOCK_MARKET_XLSX or re.search(r"india.*stock.*market", name, re.I):
        return parse_india_stock_market_xlsx(path)
    if name in _INDEX_OHLCV_FILES:
        return parse_index_ohlcv_csv(path, index_slug=_INDEX_OHLCV_FILES[name])
    if name in _CONSTITUENT_OHLCV_FILES:
        return parse_constituent_ohlcv_csv(path, index_slug=_CONSTITUENT_OHLCV_FILES[name])
    if path.suffix.lower() == ".csv":
        return _parse_historic_csv(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return parse_india_stock_market_xlsx(path)
    return pd.DataFrame()


def _dataset_path(repo_root: Path, stem: str) -> Path:
    return historic_data_dir(repo_root) / f"{stem}.parquet"


def load_india_macro_annual(repo_root: Path) -> pd.DataFrame:
    """Load annual macro from repo parquet or parse xlsx directly."""
    frame = _read_dataset(_dataset_path(repo_root, "india_macro_annual"))
    if not frame.empty:
        return frame
    xlsx = historic_data_dir(repo_root) / _INDIA_STOCK_MARKET_XLSX
    return parse_india_stock_market_xlsx(xlsx)


def load_historic_index_ohlcv(repo_root: Path, index_slug: str) -> pd.DataFrame:
    stem = f"{index_slug}_ohlcv_daily"
    frame = _read_dataset(_dataset_path(repo_root, stem))
    if not frame.empty:
        return frame
    archive_name = next((name for name, slug in _INDEX_OHLCV_FILES.items() if slug == index_slug), None)
    if archive_name is None:
        return pd.DataFrame()
    return parse_index_ohlcv_csv(historic_data_dir(repo_root) / _ARCHIVE_DIR / archive_name, index_slug=index_slug)


def load_historic_constituent_ohlcv(repo_root: Path, index_slug: str) -> pd.DataFrame:
    stem = f"{index_slug}_constituent_ohlcv_daily"
    frame = _read_dataset(_dataset_path(repo_root, stem))
    if not frame.empty:
        return frame
    archive_name = next((name for name, slug in _CONSTITUENT_OHLCV_FILES.items() if slug == index_slug), None)
    if archive_name is None:
        return pd.DataFrame()
    return parse_constituent_ohlcv_csv(
        historic_data_dir(repo_root) / _ARCHIVE_DIR / archive_name,
        index_slug=index_slug,
    )


def load_figshare_weights(repo_root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    wide_path = _dataset_path(repo_root, "nifty50_weights_monthly_wide")
    long_path = _dataset_path(repo_root, "nifty50_weights_monthly_long")
    wide = _read_dataset(wide_path)
    long = _read_dataset(long_path)
    if not wide.empty or not long.empty:
        return wide, long
    root = historic_data_dir(repo_root)
    wide_frames: list[pd.DataFrame] = []
    long_frames: list[pd.DataFrame] = []
    for subdir, source in _NIFTY50_CONSTITUENTS_SOURCES:
        wide_part, long_part = parse_figshare_weights_csv(root / subdir / "weights.csv", source=source)
        if not wide_part.empty:
            wide_frames.append(wide_part)
            long_frames.append(long_part)
    return _merge_nifty50_weights_panels(wide_frames, long_frames)


def load_figshare_sectors(repo_root: Path) -> pd.DataFrame:
    frame = _read_dataset(_dataset_path(repo_root, "nifty50_sectors"))
    if not frame.empty:
        return frame
    root = historic_data_dir(repo_root)
    sector_frames: list[pd.DataFrame] = []
    for subdir, source in _NIFTY50_CONSTITUENTS_SOURCES:
        sectors = parse_figshare_sectors_csv(root / subdir / "sectors.csv", source=source)
        if not sectors.empty:
            sector_frames.append(sectors)
    return _merge_nifty50_sector_maps(sector_frames)


def load_nifty50_constituents_summary(repo_root: Path) -> pd.DataFrame:
    frame = _read_dataset(_dataset_path(repo_root, "nifty50_constituents_membership_summary"))
    if not frame.empty:
        return frame
    summary_path = historic_data_dir(repo_root) / _CONSTITUENTS_NIFTY50_DIR / "summary.csv"
    return parse_nifty50_constituents_summary_csv(summary_path)


def parse_global_india_daily_macro(path: Path) -> pd.DataFrame:
    """Parse global-india-markets-macro/daily_market_data.csv."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("global daily macro read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty or "date" not in raw.columns:
        return pd.DataFrame()

    out = raw.copy()
    out["date"] = format_date_series(out["date"])
    out = out.dropna(subset=["date"]).sort_values("date")
    rename = {
        "nifty50_close": "nifty_close",
        "sp500_close": "sp500",
        "usd_inr_close": "usd_inr",
        "gold_close": "gold",
        "brent_close": "oil_brent",
    }
    for src, dst in rename.items():
        if src in out.columns:
            out[dst] = pd.to_numeric(out[src], errors="coerce")
    out["source"] = "historic_data_global_india_macro"
    out["source_file"] = path.name
    out["granularity"] = "daily"
    return out.drop_duplicates("date", keep="last").reset_index(drop=True)


def parse_global_india_monthly_macro(path: Path) -> pd.DataFrame:
    """Parse global-india-markets-macro/monthly_macro_data.csv."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("global monthly macro read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty or "date" not in raw.columns:
        return pd.DataFrame()

    out = raw.copy()
    out["date"] = format_date_series(out["date"])
    out = out.dropna(subset=["date"]).sort_values("date")
    for col in out.columns:
        if col not in {"date", "source", "source_file", "granularity"}:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["source"] = "historic_data_global_india_macro"
    out["source_file"] = path.name
    out["granularity"] = "monthly"
    return out.drop_duplicates("date", keep="last").reset_index(drop=True)


def parse_nifty_intraday_csv(path: Path, *, interval: str) -> pd.DataFrame:
    """Parse Nifty 50 intraday OHLCV (5m / 30m)."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("intraday read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    cols = {str(c).strip().lower(): c for c in raw.columns}
    date_col = cols.get("date")
    if date_col is None:
        return pd.DataFrame()

    rename = {cols[std]: std for std in ("open", "high", "low", "close", "volume") if std in cols}
    out = raw.rename(columns=rename)
    out["date"] = format_datetime_series(raw[date_col], utc=True)
    out = out.dropna(subset=["date"])
    for col in ("open", "high", "low", "close", "volume"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["interval"] = interval
    out["index_slug"] = "nifty50"
    out["source"] = "historic_data_archive"
    out["source_file"] = path.name
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def parse_wide_equity_panel_csv(path: Path, *, value_kind: str) -> pd.DataFrame:
    """Parse wide DATE × symbol equity panel CSV from archive (7)."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("wide equity panel read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    date_col = next((c for c in raw.columns if str(c).upper() in {"DATE", "Date"}), None)
    if date_col is None:
        return pd.DataFrame()
    out = raw.rename(columns={date_col: "date"}).copy()
    out["date"] = format_date_series(out["date"])
    out = out.dropna(subset=["date"]).sort_values("date")
    symbol_cols = [c for c in out.columns if c != "date"]
    out = out.rename(columns={col: str(col).upper() for col in symbol_cols})
    numeric_cols = [c for c in out.columns if c != "date"]
    if numeric_cols:
        out[numeric_cols] = out[numeric_cols].apply(pd.to_numeric, errors="coerce")
    out = out.assign(
        value_kind=value_kind,
        source="historic_data_archive7",
        source_file=path.name,
    )
    return out.drop_duplicates("date", keep="last").reset_index(drop=True)


def parse_india_symbol_list_csv(path: Path) -> pd.DataFrame:
    """Parse INDIA_LIST.csv symbol metadata."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("INDIA_LIST read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    cols = {str(c).strip().upper(): c for c in raw.columns}
    nse_col = cols.get("NSE_SYMBOL") or cols.get("SYMBOL")
    bse_col = cols.get("BSE_SYMBOL")
    name_col = cols.get("COMPANY_NAME") or cols.get("NAME")
    if nse_col is None:
        return pd.DataFrame()

    out = pd.DataFrame(
        {
            "symbol": raw[nse_col].astype(str).str.strip().str.upper().replace({"N/A": "", "NAN": ""}),
            "bse_symbol": raw[bse_col].astype(str).str.strip().str.upper() if bse_col else "",
            "company_name": raw[name_col].astype(str).str.strip() if name_col else "",
            "source": "historic_data_archive7",
        }
    )
    out.loc[out["symbol"].isin({"", "N/A", "NAN"}), "symbol"] = pd.NA
    out = out.dropna(subset=["symbol"])
    out = out[out["symbol"].astype(str).str.len() > 0]
    return out.drop_duplicates("symbol", keep="last").reset_index(drop=True)


def parse_news_sentiment_csv(path: Path) -> pd.DataFrame:
    """Parse News_sentiment_Jan2017_to_Apr2021.csv article-level sentiment."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("news sentiment read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    cols = {str(c).strip().lower(): c for c in raw.columns}
    date_col = cols.get("date")
    if date_col is None:
        return pd.DataFrame()
    out = pd.DataFrame(
        {
            "date": format_date_series(raw[date_col], dayfirst=True),
            "title": raw[cols["title"]] if "title" in cols else "",
            "url": raw[cols["url"]] if "url" in cols else "",
            "sentiment": raw[cols["sentiment"]].astype(str).str.upper() if "sentiment" in cols else "",
            "confidence": pd.to_numeric(raw[cols["confidence"]], errors="coerce") if "confidence" in cols else pd.NA,
            "source": "historic_data_news_sentiment",
            "source_file": path.name,
        }
    )
    return out.dropna(subset=["date"]).reset_index(drop=True)


def aggregate_news_sentiment_daily(articles: pd.DataFrame) -> pd.DataFrame:
    """Roll article sentiment into daily mean score + counts."""
    if articles.empty:
        return pd.DataFrame()
    frame = articles.copy()
    frame["signed_score"] = frame["confidence"]
    frame.loc[frame["sentiment"] == "NEGATIVE", "signed_score"] = -frame["confidence"].abs()
    frame.loc[frame["sentiment"] == "POSITIVE", "signed_score"] = frame["confidence"].abs()
    grouped = frame.groupby("date", as_index=False).agg(
        sentiment_mean=("signed_score", "mean"),
        positive_count=("sentiment", lambda s: int((s == "POSITIVE").sum())),
        negative_count=("sentiment", lambda s: int((s == "NEGATIVE").sum())),
        article_count=("sentiment", "count"),
    )
    grouped["source"] = "historic_data_news_sentiment"
    return grouped.sort_values("date").reset_index(drop=True)


def parse_indian_financial_news_csv(path: Path) -> pd.DataFrame:
    """Parse IndianFinancialNews.csv headlines."""
    if not path.is_file():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("IndianFinancialNews read failed %s: %s", path.name, exc)
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    cols = {str(c).strip().lower(): c for c in raw.columns}
    date_col = cols.get("date")
    if date_col is None:
        return pd.DataFrame()
    out = pd.DataFrame(
        {
            "date": format_date_series(raw[date_col]),
            "title": raw[cols["title"]] if "title" in cols else "",
            "description": raw[cols["description"]] if "description" in cols else "",
            "source": "historic_data_financial_news",
            "source_file": path.name,
        }
    )
    return out.dropna(subset=["date"]).reset_index(drop=True)


def load_global_india_daily_macro(repo_root: Path) -> pd.DataFrame:
    frame = _read_dataset(_dataset_path(repo_root, "global_india_daily_macro"))
    if not frame.empty:
        return frame
    path = historic_data_dir(repo_root) / _GLOBAL_MACRO_DIR / "daily_market_data.csv"
    return parse_global_india_daily_macro(path)


def load_nifty_intraday(repo_root: Path, interval: str) -> pd.DataFrame:
    stem = f"nifty50_intraday_{interval}"
    frame = _read_dataset(_dataset_path(repo_root, stem))
    if not frame.empty:
        return frame
    filename = "5min_N50_10yr.csv" if interval == "5min" else "30min_N50_10yr.csv"
    return parse_nifty_intraday_csv(historic_data_dir(repo_root) / _INTRADAY_DIR / filename, interval=interval)


def load_india_news_sentiment_daily(repo_root: Path) -> pd.DataFrame:
    frame = _read_dataset(_dataset_path(repo_root, "india_news_sentiment_daily"))
    if frame.empty:
        articles = parse_news_sentiment_csv(historic_data_dir(repo_root) / "News_sentiment_Jan2017_to_Apr2021.csv")
        frame = aggregate_news_sentiment_daily(articles)

    indic = _read_dataset(_dataset_path(repo_root, "indic_finance_sentiment_daily"))
    if indic.empty:
        indic_path = historic_data_dir(repo_root) / "indic_finance_sentiment_daily.csv"
        if indic_path.is_file():
            try:
                indic = pd.read_csv(indic_path)
            except Exception:
                indic = pd.DataFrame()

    if not indic.empty and "date" in indic.columns:
        ext = indic.copy()
        ext["date"] = ext["date"].astype(str).str[:10]
        if "sentiment_mean" not in ext.columns:
            for candidate in ("sentiment_mean", "sentiment", "mean_sentiment"):
                if candidate in ext.columns:
                    ext = ext.rename(columns={candidate: "sentiment_mean"})
                    break
        ext = ext[ext["date"] > "2021-04-15"]
        if not ext.empty and "sentiment_mean" in ext.columns:
            if frame.empty:
                frame = ext.sort_values("date").drop_duplicates("date", keep="last")
            else:
                legacy = frame.copy()
                legacy["date"] = legacy["date"].astype(str).str[:10]
                combined = concat_dataframes(legacy, ext)
                combined = combined.sort_values("date").drop_duplicates("date", keep="last")
                frame = combined.reset_index(drop=True)

    return frame


def ingest_historic_data_folder(repo_root: Path) -> dict[str, Any]:
    """Parse all files in historic_data/ and persist repo parquets."""
    check_pipeline_cancel()
    root = historic_data_dir(repo_root)
    if not root.is_dir():
        return {"status": "skipped", "reason": "missing_dir", "datasets": {}}

    manifest = _load_historic_manifest(repo_root)
    results: dict[str, Any] = {"status": "ok", "datasets": {}}
    unmapped: list[str] = []

    annual_frames: list[pd.DataFrame] = []
    for path in scan_historic_data_dir(repo_root):
        check_pipeline_cancel()
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in _HANDLED_SUBDIRS:
            continue
        if path.name in {
            "News_sentiment_Jan2017_to_Apr2021.csv",
            "IndianFinancialNews.csv",
            "Nifty50.csv",
        }:
            continue
        if path.suffix.lower() in {".xlsx", ".xls"} or path.name == _INDIA_STOCK_MARKET_XLSX:
            parsed = parse_india_stock_market_xlsx(path)
            if not parsed.empty:
                annual_frames.append(parsed)
            else:
                unmapped.append(str(rel))
        elif path.suffix.lower() == ".csv" and path.name.startswith("india_macro"):
            parsed = _parse_historic_csv(path)
            if not parsed.empty:
                annual_frames.append(parsed)
            else:
                unmapped.append(str(rel))
        elif path.name in _LOCAL_NIFTY50_VALUATION_NAMES or _is_nifty50_valuation_csv(path):
            continue
        elif path.name in _LOCAL_NIFTY50_LIST_NAMES:
            parsed = parse_ind_nifty50_list_csv(path)
            if parsed.get("status") == "ok":
                out_json = historic_data_dir(repo_root) / "ind_nifty50_constituents_current.json"
                out_json.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
                results["datasets"]["ind_nifty50_constituents_current"] = {
                    "count": parsed.get("count"),
                    "path": str(out_json),
                    "source_file": path.name,
                }
            else:
                unmapped.append(str(rel))
        elif path.name == "Fii Dii Trading activity.csv":
            out_path = _dataset_path(repo_root, "fii_dii_trading_activity")
            if _should_skip_historic_dataset(
                manifest=manifest,
                dataset_key="fii_dii_trading_activity",
                source_path=path,
                out_path=out_path,
            ):
                _record_skipped_historic_dataset(
                    results,
                    manifest,
                    dataset_key="fii_dii_trading_activity",
                    out_path=out_path,
                )
            else:
                parsed = parse_fii_dii_trading_activity_csv(path)
                _ingest_frame_with_manifest(
                    repo_root,
                    manifest,
                    results,
                    dataset_key="fii_dii_trading_activity",
                    source_path=path,
                    frame=parsed,
                    meta={"rows": len(parsed)},
                )
        elif path.name == "india_cpi_monthly_yoy.csv":
            parsed = parse_india_cpi_monthly_csv(path)
            if not parsed.empty:
                out_path = _dataset_path(repo_root, "india_cpi_monthly_yoy")
                _write_dataset(parsed, out_path)
                results["datasets"]["india_cpi_monthly_yoy"] = {"rows": len(parsed), "path": str(out_path)}
            else:
                unmapped.append(str(rel))
        elif path.name == "nifty_oi_ data.csv":
            parsed = parse_nifty_fo_oi_daily_csv(path)
            if not parsed.empty:
                out_path = _dataset_path(repo_root, "nifty_oi_daily")
                _write_dataset(parsed, out_path)
                results["datasets"]["nifty_oi_daily"] = {"rows": len(parsed), "path": str(out_path)}
            else:
                unmapped.append(str(rel))
        elif path.name == "nifty50_fo_data_filtered.csv":
            out_path = _dataset_path(repo_root, "nifty50_fo_panel")
            if _should_skip_historic_dataset(
                manifest=manifest,
                dataset_key="nifty50_fo_panel",
                source_path=path,
                out_path=out_path,
            ):
                _record_skipped_historic_dataset(
                    results,
                    manifest,
                    dataset_key="nifty50_fo_panel",
                    out_path=out_path,
                    extra={"note": "capped_rows"},
                )
            else:
                parsed = parse_nifty50_fo_panel_csv(path)
                _ingest_frame_with_manifest(
                    repo_root,
                    manifest,
                    results,
                    dataset_key="nifty50_fo_panel",
                    source_path=path,
                    frame=parsed,
                    meta={"rows": len(parsed), "note": "capped_rows"},
                )
        elif path.suffix.lower() == ".csv":
            unmapped.append(str(rel))

    if annual_frames:
        merged = concat_frames(annual_frames)
        if "year" in merged.columns:
            merged = merged.sort_values("year").drop_duplicates("year", keep="last")
        elif "date" in merged.columns:
            merged = merged.sort_values("date").drop_duplicates("date", keep="last")
        out_path = _dataset_path(repo_root, "india_macro_annual")
        _write_dataset(merged, out_path)
        results["datasets"]["india_macro_annual"] = {
            "rows": len(merged),
            "path": str(out_path),
            "start_year": int(merged["year"].iloc[0]) if "year" in merged.columns else None,
            "end_year": int(merged["year"].iloc[-1]) if "year" in merged.columns else None,
        }

    archive_dir = root / _ARCHIVE_DIR
    for filename, index_slug in _INDEX_OHLCV_FILES.items():
        path = archive_dir / filename
        dataset_key = f"{index_slug}_ohlcv_daily"
        out_path = _dataset_path(repo_root, dataset_key)
        if _should_skip_historic_dataset(
            manifest=manifest,
            dataset_key=dataset_key,
            source_path=path,
            out_path=out_path,
        ):
            _record_skipped_historic_dataset(
                results,
                manifest,
                dataset_key=dataset_key,
                out_path=out_path,
            )
            continue
        frame = parse_index_ohlcv_csv(path, index_slug=index_slug)
        if frame.empty:
            continue
        meta = {
            "rows": len(frame),
            "start": str(frame["date"].iloc[0]),
            "end": str(frame["date"].iloc[-1]),
        }
        _ingest_frame_with_manifest(
            repo_root,
            manifest,
            results,
            dataset_key=dataset_key,
            source_path=path,
            frame=frame,
            meta=meta,
        )

    for filename, index_slug in _CONSTITUENT_OHLCV_FILES.items():
        path = archive_dir / filename
        dataset_key = f"{index_slug}_constituent_ohlcv_daily"
        out_path = _dataset_path(repo_root, dataset_key)
        if _should_skip_historic_dataset(
            manifest=manifest,
            dataset_key=dataset_key,
            source_path=path,
            out_path=out_path,
        ):
            _record_skipped_historic_dataset(
                results,
                manifest,
                dataset_key=dataset_key,
                out_path=out_path,
                extra={"symbols": int((manifest.get("datasets") or {}).get(dataset_key, {}).get("symbols") or 0)},
            )
            continue
        frame = parse_constituent_ohlcv_csv(path, index_slug=index_slug)
        if frame.empty:
            continue
        meta = {
            "rows": len(frame),
            "symbols": int(frame["symbol"].nunique()),
            "start": str(frame["date"].iloc[0]),
            "end": str(frame["date"].iloc[-1]),
        }
        _ingest_frame_with_manifest(
            repo_root,
            manifest,
            results,
            dataset_key=dataset_key,
            source_path=path,
            frame=frame,
            meta=meta,
        )

    figshare_dir = root / _FIGSHARE_DIR
    constituents_dir = root / _CONSTITUENTS_NIFTY50_DIR
    wide_frames: list[pd.DataFrame] = []
    long_frames: list[pd.DataFrame] = []
    sector_frames: list[pd.DataFrame] = []
    for subdir, source in _NIFTY50_CONSTITUENTS_SOURCES:
        folder = root / subdir
        wide_part, long_part = parse_figshare_weights_csv(folder / "weights.csv", source=source)
        if not wide_part.empty:
            wide_frames.append(wide_part)
            long_frames.append(long_part)
        sectors_part = parse_figshare_sectors_csv(folder / "sectors.csv", source=source)
        if not sectors_part.empty:
            sector_frames.append(sectors_part)

    wide, long = _merge_nifty50_weights_panels(wide_frames, long_frames)
    if not wide.empty:
        wide_path = _dataset_path(repo_root, "nifty50_weights_monthly_wide")
        long_path = _dataset_path(repo_root, "nifty50_weights_monthly_long")
        _write_dataset(wide, wide_path)
        _write_dataset(long, long_path)
        results["datasets"]["nifty50_weights_monthly"] = {
            "months": len(wide),
            "membership_rows": len(long),
            "symbols_tracked": len([c for c in wide.columns if c not in {"date", "source"}]),
            "start": str(wide["date"].iloc[0]),
            "end": str(wide["date"].iloc[-1]),
            "wide_path": str(wide_path),
            "long_path": str(long_path),
            "sources": [source for subdir, source in _NIFTY50_CONSTITUENTS_SOURCES if (root / subdir / "weights.csv").is_file()],
        }

    sectors = _merge_nifty50_sector_maps(sector_frames)
    if not sectors.empty:
        sectors_path = _dataset_path(repo_root, "nifty50_sectors")
        _write_dataset(sectors, sectors_path)
        results["datasets"]["nifty50_sectors"] = {
            "rows": len(sectors),
            "path": str(sectors_path),
        }

    summary = parse_nifty50_constituents_summary_csv(constituents_dir / "summary.csv")
    if not summary.empty:
        summary_path = _dataset_path(repo_root, "nifty50_constituents_membership_summary")
        _write_dataset(summary, summary_path)
        results["datasets"]["nifty50_constituents_membership_summary"] = {
            "rows": len(summary),
            "path": str(summary_path),
        }

    macro_dir = root / _GLOBAL_MACRO_DIR
    daily_macro = parse_global_india_daily_macro(macro_dir / "daily_market_data.csv")
    if not daily_macro.empty:
        daily_path = _dataset_path(repo_root, "global_india_daily_macro")
        _write_dataset(daily_macro, daily_path)
        results["datasets"]["global_india_daily_macro"] = {
            "rows": len(daily_macro),
            "path": str(daily_path),
            "start": str(daily_macro["date"].iloc[0]),
            "end": str(daily_macro["date"].iloc[-1]),
        }
    monthly_macro = parse_global_india_monthly_macro(macro_dir / "monthly_macro_data.csv")
    if not monthly_macro.empty:
        monthly_path = _dataset_path(repo_root, "global_india_monthly_macro")
        _write_dataset(monthly_macro, monthly_path)
        results["datasets"]["global_india_monthly_macro"] = {
            "rows": len(monthly_macro),
            "path": str(monthly_path),
            "start": str(monthly_macro["date"].iloc[0]),
            "end": str(monthly_macro["date"].iloc[-1]),
        }

    intraday_dir = root / _INTRADAY_DIR
    for interval, filename in (("5min", "5min_N50_10yr.csv"), ("30min", "30min_N50_10yr.csv")):
        path = intraday_dir / filename
        dataset_key = f"nifty50_intraday_{interval}"
        out_path = _dataset_path(repo_root, dataset_key)
        if _should_skip_historic_dataset(
            manifest=manifest,
            dataset_key=dataset_key,
            source_path=path,
            out_path=out_path,
        ):
            _record_skipped_historic_dataset(
                results,
                manifest,
                dataset_key=dataset_key,
                out_path=out_path,
            )
            continue
        frame = parse_nifty_intraday_csv(path, interval=interval)
        if frame.empty:
            continue
        meta = {
            "rows": len(frame),
            "start": str(frame["date"].iloc[0]),
            "end": str(frame["date"].iloc[-1]),
        }
        _ingest_frame_with_manifest(
            repo_root,
            manifest,
            results,
            dataset_key=dataset_key,
            source_path=path,
            frame=frame,
            meta=meta,
        )

    equity_dir = root / _EQUITY_PANEL_DIR
    panel_specs = [
        ("26YRS_closes_20260420_171303.csv", "india_equity_closes_26y_wide", "close"),
        ("26YRS_volume_20260420_171303.csv", "india_equity_volume_26y_wide", "volume"),
        ("gt_10_yrs_data.csv", "india_equity_closes_gt10y_wide", "close"),
        ("all_20_yrs_data.csv", "india_equity_closes_all20y_wide", "close"),
        ("combined_stock_data.csv", "india_equity_closes_combined_wide", "close"),
    ]
    for filename, stem, value_kind in panel_specs:
        check_pipeline_cancel()
        path = equity_dir / filename
        out_path = _dataset_path(repo_root, stem)
        if _should_skip_historic_dataset(
            manifest=manifest,
            dataset_key=stem,
            source_path=path,
            out_path=out_path,
        ):
            prev = (manifest.get("datasets") or {}).get(stem) or {}
            results["datasets"][stem] = {
                "rows": int(prev.get("rows") or 0),
                "symbols": int(prev.get("symbols") or 0),
                "path": str(out_path),
                "skipped": True,
                "start": prev.get("start"),
                "end": prev.get("end"),
            }
            continue
        frame = parse_wide_equity_panel_csv(path, value_kind=value_kind)
        if frame.empty:
            continue
        meta = {
            "rows": len(frame),
            "symbols": len([c for c in frame.columns if c not in {"date", "source", "source_file", "value_kind"}]),
            "start": str(frame["date"].iloc[0]),
            "end": str(frame["date"].iloc[-1]),
        }
        if _write_dataset_if_changed(
            repo_root,
            manifest,
            dataset_key=stem,
            source_path=path,
            frame=frame,
            out_path=out_path,
            meta=meta,
        ):
            results["datasets"][stem] = {"path": str(out_path), **meta}

    symbol_list = parse_india_symbol_list_csv(equity_dir / "INDIA_LIST.csv")
    if not symbol_list.empty:
        list_path = _dataset_path(repo_root, "india_symbol_list")
        _write_dataset(symbol_list, list_path)
        results["datasets"]["india_symbol_list"] = {
            "rows": len(symbol_list),
            "path": str(list_path),
        }

    news_path = root / "News_sentiment_Jan2017_to_Apr2021.csv"
    articles_path = _dataset_path(repo_root, "india_news_sentiment_articles")
    daily_path = _dataset_path(repo_root, "india_news_sentiment_daily")
    if _should_skip_historic_dataset(
        manifest=manifest,
        dataset_key="india_news_sentiment_daily",
        source_path=news_path,
        out_path=daily_path,
    ):
        _record_skipped_historic_dataset(
            results,
            manifest,
            dataset_key="india_news_sentiment",
            out_path=daily_path,
        )
    else:
        articles = parse_news_sentiment_csv(news_path)
        if not articles.empty:
            _write_dataset(articles, articles_path)
            daily_sentiment = aggregate_news_sentiment_daily(articles)
            _write_dataset(daily_sentiment, daily_path)
            _record_historic_dataset(
                manifest,
                dataset_key="india_news_sentiment_daily",
                source_path=news_path,
                out_path=daily_path,
                meta={
                    "rows": len(daily_sentiment),
                    "articles": len(articles),
                    "start": str(daily_sentiment["date"].iloc[0]),
                    "end": str(daily_sentiment["date"].iloc[-1]),
                },
            )
            results["datasets"]["india_news_sentiment"] = {
                "articles": len(articles),
                "daily_rows": len(daily_sentiment),
                "articles_path": str(articles_path),
                "daily_path": str(daily_path),
                "start": str(daily_sentiment["date"].iloc[0]),
                "end": str(daily_sentiment["date"].iloc[-1]),
            }

    fin_news = parse_indian_financial_news_csv(root / "IndianFinancialNews.csv")
    if not fin_news.empty:
        fin_path = _dataset_path(repo_root, "india_financial_news")
        _write_dataset(fin_news, fin_path)
        results["datasets"]["india_financial_news"] = {
            "rows": len(fin_news),
            "path": str(fin_path),
            "start": str(fin_news["date"].iloc[0]),
            "end": str(fin_news["date"].iloc[-1]),
        }

    mrchartist_path = root / _MRCHARTIST_HISTORY_JSON
    mrchartist = parse_mrchartist_history_json(mrchartist_path)
    if not mrchartist.empty:
        mr_path = _dataset_path(repo_root, "mrchartist_flow_daily")
        _write_dataset(mrchartist, mr_path)
        results["datasets"]["mrchartist_flow_daily"] = {
            "rows": len(mrchartist),
            "path": str(mr_path),
            "start": str(mrchartist["date"].iloc[0]),
            "end": str(mrchartist["date"].iloc[-1]),
            "source_file": mrchartist_path.name,
        }

    indic_path = root / _INDIC_FINANCE_CSV
    indic_articles = parse_indic_finance_csv(indic_path)
    if not indic_articles.empty:
        indic_articles_path = _dataset_path(repo_root, "indic_finance_articles")
        _write_dataset(indic_articles, indic_articles_path)
        indic_daily = aggregate_indic_finance_daily(indic_articles)
        indic_daily_path = _dataset_path(repo_root, "indic_finance_sentiment_daily")
        _write_dataset(indic_daily, indic_daily_path)
        results["datasets"]["indic_finance_sentiment"] = {
            "articles": len(indic_articles),
            "daily_rows": len(indic_daily),
            "articles_path": str(indic_articles_path),
            "daily_path": str(indic_daily_path),
            "start": str(indic_daily["date"].iloc[0]),
            "end": str(indic_daily["date"].iloc[-1]),
        }

    niftyindices_dir = root / _NIFTYINDICES_DIR
    if niftyindices_dir.is_dir():
        ohlcv_frames: list[pd.DataFrame] = []
        for path in sorted(niftyindices_dir.glob("*.csv")):
            slug = "nifty50" if "nifty" in path.name.lower() else "index"
            frame = parse_niftyindices_price_csv(path, index_slug=slug)
            if not frame.empty:
                ohlcv_frames.append(frame)
        if ohlcv_frames:
            incoming = concat_frames(ohlcv_frames)
            existing = _read_dataset(_dataset_path(repo_root, "nifty50_ohlcv_daily"))
            merged = _merge_index_ohlcv(existing, incoming)
            out_path = _dataset_path(repo_root, "nifty50_ohlcv_daily")
            _write_dataset(merged, out_path)
            results["datasets"]["nifty50_ohlcv_daily"] = {
                "rows": len(merged),
                "path": str(out_path),
                "start": str(merged["date"].iloc[0]),
                "end": str(merged["date"].iloc[-1]),
                "sources": [str(p.name) for p in sorted(niftyindices_dir.glob("*.csv"))],
            }

    rbi_dir = root / _RBI_DIR
    rbi_frames: list[pd.DataFrame] = []
    for path in sorted(rbi_dir.glob("*.csv")):
        frame = parse_rbi_wss_ratios_csv(path)
        if not frame.empty:
            rbi_frames.append(frame)
    if rbi_frames:
        rbi_merged = concat_frames(rbi_frames).sort_values("date").drop_duplicates("date", keep="last")
        rbi_path = _dataset_path(repo_root, "india_rbi_wss_weekly")
        _write_dataset(rbi_merged, rbi_path)
        results["datasets"]["india_rbi_wss_weekly"] = {
            "rows": len(rbi_merged),
            "path": str(rbi_path),
            "start": str(rbi_merged["date"].iloc[0]),
            "end": str(rbi_merged["date"].iloc[-1]),
        }

    if not results["datasets"]:
        return {"status": "skipped", "reason": "no_parsed_rows", "unmapped_files": unmapped}

    if unmapped:
        results["unmapped_files"] = unmapped

    valuation_paths = discover_nifty50_valuation_csvs(repo_root)
    valuation_frames = [parse_nifty50_pe_pb_div_csv(path) for path in valuation_paths]
    valuation_merged = _merge_nifty50_valuation_frames(valuation_frames)
    if not valuation_merged.empty:
        out_path = _dataset_path(repo_root, "nifty50_valuation_daily")
        _write_dataset(valuation_merged, out_path)
        results["datasets"]["nifty50_valuation_daily"] = {
            "rows": len(valuation_merged),
            "path": str(out_path),
            "start": str(valuation_merged["date"].iloc[0]),
            "end": str(valuation_merged["date"].iloc[-1]),
            "source_files": [path.name for path in valuation_paths],
        }

    results["rows"] = sum(int(v.get("rows") or v.get("months") or 0) for v in results["datasets"].values())
    _save_historic_manifest(repo_root, manifest)
    return results
