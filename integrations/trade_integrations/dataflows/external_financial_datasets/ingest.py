"""External financial datasets — Hugging Face, Kaggle, curated registries."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.throttled_http import fetch_to_path

logger = logging.getLogger(__name__)
HUB_SUBDIR = "_data/equities/external"


def hub_data_dir() -> Path:
    return get_hub_dir() / HUB_SUBDIR


def _cache_dir() -> Path:
    root = Path(os.environ.get("TRADE_STACK_ROOT", Path(__file__).resolve().parents[4])).expanduser()
    if custom := os.environ.get("EXTERNAL_FINANCIAL_CACHE", "").strip():
        path = Path(custom).expanduser()
        return (root / path if not path.is_absolute() else path).resolve()
    return (root / "data" / "external_financial").resolve()


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_parquet(path, index=False)
    except ImportError:
        frame.to_csv(path.with_suffix(".csv"), index=False)


# Hugging Face — Chiron-S/NSE_Stocks_Data (community NSE OHLCV 2016–2024)
HF_NSE_DATASET = {
    "repo_id": "Chiron-S/NSE_Stocks_Data",
    "files": (
        "NSE_Stocks_2016_2020.parquet",
        "NSE_Stocks_2021_2024.parquet",
    ),
    "source_url": "https://huggingface.co/datasets/Chiron-S/NSE_Stocks_Data",
}

# Kaggle — configure via env; requires ~/.kaggle/kaggle.json or KAGGLE_* env vars
KAGGLE_DATASETS: tuple[dict[str, str], ...] = (
    {
        "key": "nifty50_historical",
        "slug": os.environ.get("KAGGLE_NIFTY50_DATASET", "rahuldua/nifty-50-stock-historical-data"),
        "note": "Set KAGGLE_USERNAME + KAGGLE_KEY or ~/.kaggle/kaggle.json",
    },
    {
        "key": "us_macro",
        "slug": os.environ.get("KAGGLE_US_MACRO_DATASET", "fredericopratto/us-macroeconomic-time-series"),
        "note": "US Macroeconomic Time Series archive on Kaggle",
    },
)


def _hf_resolve_url(repo_id: str, filename: str) -> str:
    return f"https://huggingface.co/datasets/{repo_id}/resolve/main/{filename}"


def _aggregate_intraday_to_daily(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    work["symbol"] = work["symbol"].astype(str).str.strip().str.upper()
    for col in ("open", "high", "low", "close", "volume"):
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["date", "symbol", "close"])
    daily = (
        work.groupby(["date", "symbol"], as_index=False)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .sort_values(["date", "symbol"])
        .reset_index(drop=True)
    )
    daily["source"] = "huggingface_nse"
    return daily


def ingest_huggingface_nse(*, force_fetch: bool = False) -> dict[str, Any]:
    """Download HF NSE stock parquet shards and persist daily OHLCV cold tier."""
    cache = _cache_dir() / "huggingface" / "nse_stocks"
    cache.mkdir(parents=True, exist_ok=True)
    repo_id = HF_NSE_DATASET["repo_id"]
    frames: list[pd.DataFrame] = []

    for filename in HF_NSE_DATASET["files"]:
        dest = cache / filename
        if not dest.is_file() or force_fetch:
            url = _hf_resolve_url(repo_id, filename)
            logger.info("Fetching HF %s", url)
            fetch_to_path(url, dest, force=force_fetch)
        frames.append(pd.read_parquet(dest))

    combined = pd.concat(frames, ignore_index=True)
    daily = _aggregate_intraday_to_daily(combined)

    out_dir = hub_data_dir() / "huggingface_nse"
    out_dir.mkdir(parents=True, exist_ok=True)
    panel_path = out_dir / "nse_equity_ohlcv_daily.parquet"
    _write_parquet(daily, panel_path)

    symbols = int(daily["symbol"].nunique())
    cold_result = {
        "status": "ok",
        "dataset": "nse_equity_ohlcv_hf_daily",
        "path": str(panel_path),
        "rows": len(daily),
        "symbols": symbols,
        "start": str(daily["date"].iloc[0]) if not daily.empty else None,
        "end": str(daily["date"].iloc[-1]) if not daily.empty else None,
        "note": "Panel stored under hub _data (not history_store — multi-symbol rows)",
    }
    return {
        "status": "ok",
        "source": HF_NSE_DATASET["source_url"],
        "rows": len(daily),
        "symbols": int(symbols),
        "start": str(daily["date"].iloc[0]) if not daily.empty else None,
        "end": str(daily["date"].iloc[-1]) if not daily.empty else None,
        "cold_tier": cold_result,
        "merged_into_macro_daily": False,
        "note": "Per-symbol equity panel; not merged into macro_daily (use for constituent backfill)",
    }


def ingest_kaggle_datasets() -> dict[str, Any]:
    """Optional Kaggle ingest when credentials are configured."""
    results: dict[str, Any] = {}
    kaggle_json = Path.home() / ".kaggle" / "kaggle.json"
    has_env = bool(os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"))
    if not kaggle_json.is_file() and not has_env:
        return {
            "status": "skipped",
            "reason": "no_kaggle_credentials",
            "note": "Install kagglehub + set ~/.kaggle/kaggle.json or KAGGLE_USERNAME/KAGGLE_KEY",
            "datasets": {d["key"]: {"status": "skipped", "slug": d["slug"]} for d in KAGGLE_DATASETS},
        }

    try:
        import kagglehub
    except ImportError:
        return {
            "status": "skipped",
            "reason": "kagglehub_not_installed",
            "note": "pip install kagglehub to enable Kaggle archives",
        }

    cache = _cache_dir() / "kaggle"
    cache.mkdir(parents=True, exist_ok=True)

    for spec in KAGGLE_DATASETS:
        key = spec["key"]
        slug = spec["slug"]
        try:
            path = kagglehub.dataset_download(slug, path=cache / key)
            results[key] = {
                "status": "ok",
                "slug": slug,
                "path": str(path),
                "merged_into_macro_daily": False,
                "note": "Downloaded to cache; wire parser when schema confirmed",
            }
        except Exception as exc:
            logger.warning("Kaggle ingest failed for %s: %s", slug, exc)
            results[key] = {"status": "error", "slug": slug, "error": str(exc)}

    return {"status": "partial" if any(v.get("status") == "error" for v in results.values()) else "ok", "datasets": results}


def ingest_archive_org_collections() -> dict[str, Any]:
    """Archive.org economic collections — no stable CSV endpoints found for Nifty/macro."""
    return {
        "status": "skipped",
        "reason": "no_curated_csv_endpoints",
        "note": "Archive.org search returned no direct Nifty/US-macro CSV mirrors; "
        "GitHub datasets/* + Hugging Face cover the same sources (FRED, World Bank, CBOE)",
        "search_url": "https://archive.org/search?query=economic+time+series",
    }


def load_nse_equity_hf_daily() -> pd.DataFrame:
    """Load Hugging Face NSE daily OHLCV panel from hub."""
    path = hub_data_dir() / "huggingface_nse" / "nse_equity_ohlcv_daily.parquet"
    csv_path = path.with_suffix(".csv")
    if path.is_file():
        try:
            return pd.read_parquet(path)
        except Exception:
            pass
    if csv_path.is_file():
        return pd.read_csv(csv_path)
    return pd.DataFrame()


def verify_external_financial_datasets() -> dict[str, Any]:
    """Verify HF/Kaggle cold-tier presence and merge policy."""
    nse = load_nse_equity_hf_daily()
    report: dict[str, Any] = {
        "hub_dir": str(hub_data_dir()),
        "huggingface_nse": {
            "hub_path": str(hub_data_dir() / "huggingface_nse" / "nse_equity_ohlcv_daily.parquet"),
            "rows": len(nse),
            "symbols": int(nse["symbol"].nunique()) if not nse.empty and "symbol" in nse.columns else 0,
            "merged_into_macro_daily": False,
        },
        "kaggle": {"status": "run_ingest_to_check", "configured_slugs": [d["slug"] for d in KAGGLE_DATASETS]},
        "archive_org": ingest_archive_org_collections(),
    }
    manifest_path = hub_data_dir() / "manifest.json"
    if manifest_path.is_file():
        report["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
    return report


def ingest_external_financial_datasets(
    *,
    force_fetch: bool = False,
    include_huggingface: bool = True,
    include_kaggle: bool = True,
    include_archive: bool = True,
    skip_curated: bool = False,
) -> dict[str, Any]:
    """Orchestrate Hugging Face, Kaggle, and Archive.org ingest passes."""
    results: dict[str, Any] = {}

    if include_huggingface:
        try:
            results["huggingface_nse"] = ingest_huggingface_nse(force_fetch=force_fetch)
        except Exception as exc:
            logger.warning("HF NSE ingest failed: %s", exc)
            results["huggingface_nse"] = {"status": "error", "error": str(exc)}

    if include_kaggle:
        results["kaggle"] = ingest_kaggle_datasets()

    if include_archive:
        results["archive_org"] = ingest_archive_org_collections()

    from trade_integrations.dataflows.external_financial_datasets.curated_ingest import ingest_curated_market_data

    if not skip_curated:
        results["curated_market"] = ingest_curated_market_data(
            force_fetch=force_fetch,
            include_kaggle=include_kaggle,
        )

    manifest = {
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "sources": [
            HF_NSE_DATASET["source_url"],
            "https://huggingface.co/datasets",
            "https://www.kaggle.com/datasets",
            "https://archive.org",
            "https://github.com/awesomedata/awesome-public-datasets",
        ],
        "results": results,
    }
    out_dir = hub_data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    verification = verify_external_financial_datasets()
    return {"status": "ok", "results": results, "verification": verification, "manifest": manifest}
