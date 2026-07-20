"""Full source audit — inventory external repos/datasets and ingest keyword-relevant gaps."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from trade_integrations.http import get

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.github_datasets.config import DATASETS, raw_url
from trade_integrations.dataflows.throttled_http import fetch_delay_sec, fetch_to_path

logger = logging.getLogger(__name__)

HUB_SUBDIR = "_data/source_audit"
_UA = "trade-stack-research/0.1 (+https://github.com/p1927/trade)"

DATA_EXTENSIONS = frozenset({".csv", ".json", ".parquet", ".xlsx", ".xls", ".tsv"})

RELEVANCE = re.compile(
    r"nifty|sensex|nse|bse|fii|dii|fpi|constituent|weight|pe|pb|div|yield|valuation|"
    r"flow|sector|ohlcv|macro|vix|bond|gold|exchange|forex|calendar|event|eps|index|"
    r"equity|stock|oi|pcr|fut|option|cpi|gdp|sentiment|news|fundamental|balance|profit",
    re.I,
)

SKIP_PATH = re.compile(
    r"telegram|agent_|package-lock|package\.json|manifest\.json|updates\.json|"
    r"\.ipynb|validate\.py|README|LICENSE|CHANGELOG|validation_report|docs/20[0-9]{2}/.*sp500",
    re.I,
)

GITHUB_SOURCES: tuple[dict[str, str], ...] = (
    {"id": "mrchartist", "repo": "MrChartist/fii-dii-data", "branch": "main"},
    {"id": "vishalvx", "repo": "vishalvx/nifty-indices-datasets", "branch": "main"},
    {"id": "nifty100_intel", "repo": "Samadhan1904/nifty100-financial-intelligence", "branch": "main"},
    {"id": "rswarnkar", "repo": "RSwarnkar/nifty50-scrapping", "branch": "main"},
    {"id": "yfiua", "repo": "yfiua/index-constituents", "branch": "main", "india_only": "false"},
)

HF_SOURCES: tuple[dict[str, str], ...] = (
    {
        "id": "forex_factory",
        "repo_id": "Ehsanrs2/Forex_Factory_Calendar",
        "files": "forex_factory_cache.csv",
    },
    {
        "id": "nse_stocks",
        "repo_id": "Chiron-S/NSE_Stocks_Data",
        "files": "NSE_Stocks_2016_2020.parquet,NSE_Stocks_2021_2024.parquet",
    },
)

KAGGLE_SLUGS: tuple[str, ...] = (
    "obiwankanobi/nifty-50-historical-pe-pb-div-yield-eps-and-close",
    "rahuldua/nifty-50-stock-historical-data",
    "fredericopratto/us-macroeconomic-time-series",
)


def hub_dir() -> Path:
    return get_hub_dir() / HUB_SUBDIR


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_parquet(path, index=False)
    except ImportError:
        frame.to_csv(path.with_suffix(".csv"), index=False)


def _is_relevant(path: str) -> bool:
    if SKIP_PATH.search(path):
        return False
    if Path(path).suffix.lower() not in DATA_EXTENSIONS:
        return False
    return bool(RELEVANCE.search(path))


def _github_tree(repo: str, branch: str) -> list[dict[str, Any]]:
    url = f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
    resp = get(url, headers={"User-Agent": _UA}, timeout=90)
    if resp.status_code != 200:
        logger.warning("GitHub tree failed %s: %s", repo, resp.status_code)
        return []
    return [t for t in resp.json().get("tree", []) if t.get("type") == "blob"]


def _hub_has_file(rel_path: str) -> bool:
    """Check curated/cache paths for prior download."""
    roots = [
        get_hub_dir() / "_data/curated_market/cache",
        get_hub_dir() / "_data/curated_market/flows",
        get_hub_dir() / "_data/curated_market/indices",
        get_hub_dir() / "_data/equities/external",
        get_hub_dir() / "_data/macro/github_datasets",
        get_hub_dir() / "_data/fundamentals/nifty100",
        hub_dir() / "raw",
        hub_dir() / "local_historic",
    ]
    name = Path(rel_path).name
    cache_name = name.replace("/", "_")
    for root in roots:
        if not root.is_dir():
            continue
        if list(root.rglob(name)) or list(root.rglob(cache_name)) or list(root.rglob(f"data_{name}")):
            return True
    return False


def audit_github_sources() -> dict[str, Any]:
    report: dict[str, Any] = {}
    for spec in GITHUB_SOURCES:
        sid = spec["id"]
        blobs = _github_tree(spec["repo"], spec["branch"])
        relevant = [b for b in blobs if _is_relevant(b["path"])]
        if spec.get("india_only") == "false":
            india = [b for b in relevant if re.search(r"nifty|nse|india|sensex|bse", b["path"], re.I)]
            report[sid] = {
                "repo": spec["repo"],
                "total_blobs": len(blobs),
                "relevant_files": len(relevant),
                "india_relevant_files": len(india),
                "note": "No Nifty 50 in yfiua — SP500/NASDAQ/HSI only" if sid == "yfiua" else None,
                "files": [b["path"] for b in relevant[:50]],
                "truncated": len(relevant) > 50,
            }
        else:
            ingested = sum(1 for b in relevant if _hub_has_file(b["path"]))
            gaps = [b["path"] for b in relevant if not _hub_has_file(b["path"])]
            report[sid] = {
                "repo": spec["repo"],
                "total_blobs": len(blobs),
                "relevant_files": len(relevant),
                "ingested_estimate": ingested,
                "gaps": gaps,
                "files": [b["path"] for b in relevant],
            }
    return report


def audit_github_datasets_repos() -> dict[str, Any]:
    """List all CSV paths in configured datasets/* repos vs what we fetch."""
    report: dict[str, Any] = {}
    seen_repos: set[str] = set()
    for spec in DATASETS:
        repo = str(spec["repo"])
        if repo in seen_repos:
            continue
        seen_repos.add(repo)
        branch = str(spec["branch"])
        blobs = _github_tree(repo, branch)
        csvs = [b["path"] for b in blobs if b["path"].lower().endswith(".csv")]
        configured = [str(s["path"]) for s in DATASETS if s["repo"] == repo]
        report[repo] = {
            "all_csv": csvs,
            "configured": configured,
            "unconfigured_csv": [p for p in csvs if p not in configured],
        }
    return report


def audit_hf_sources() -> dict[str, Any]:
    report: dict[str, Any] = {}
    for spec in HF_SOURCES:
        files = [f.strip() for f in spec["files"].split(",") if f.strip()]
        report[spec["id"]] = {
            "repo_id": spec["repo_id"],
            "expected_files": files,
            "ingested": [_hub_has_file(f) for f in files],
        }
    return report


def audit_kaggle_sources() -> dict[str, Any]:
    has_creds = Path.home().joinpath(".kaggle/kaggle.json").is_file() or bool(
        os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY")
    )
    return {
        "credentials": has_creds,
        "slugs": list(KAGGLE_SLUGS),
        "status": "ready" if has_creds else "skipped_no_credentials",
    }


def audit_local_historic() -> dict[str, Any]:
    from trade_integrations.nse_browser.parsers.historic_data import historic_data_dir, scan_historic_data_dir
    from trade_integrations.nse_browser.repository import repo_root

    root = historic_data_dir(repo_root())
    if not root.is_dir():
        return {"status": "skipped", "reason": "missing_dir"}

    all_paths = scan_historic_data_dir(repo_root())
    data_files = [p for p in all_paths if p.suffix.lower() in DATA_EXTENSIONS]
    relevant = [p for p in data_files if _is_relevant(str(p.relative_to(root)))]
    parquet_stems = {p.stem for p in root.glob("*.parquet")}

    unmapped: list[str] = []
    for path in relevant:
        rel = str(path.relative_to(root))
        stem = path.stem.replace(" ", "_").lower()
        if stem not in parquet_stems and path.name not in {
            "ind_nifty50list.csv",
            "NIFTY 50_Historical_PE_PB_DIV.csv",
            "nifty50_historical_pe_pb_div.csv",
        }:
            unmapped.append(rel)

    return {
        "root": str(root),
        "total_data_files": len(data_files),
        "relevant_files": len(relevant),
        "parquet_datasets": len(parquet_stems),
        "unmapped_relevant": unmapped,
        "all_relevant": [str(p.relative_to(root)) for p in relevant],
    }


def audit_web_only_sources() -> dict[str, Any]:
    return {
        "primeinvestor": {"status": "web_only", "bulk": False},
        "stockedge_fii_dii": {"status": "web_only", "bulk": False},
        "tapetide_mcp": {"status": "live_mcp", "env": "TAPETIDE_TOKEN"},
        "niftyindices_live": {"status": "web_csv", "fallback": "local ind_nifty50list.csv"},
    }


def run_full_source_audit() -> dict[str, Any]:
    """Inventory all mentioned sources; report gaps."""
    audit = {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "fetch_delay_sec": fetch_delay_sec(),
        "github_curated": audit_github_sources(),
        "github_datasets": audit_github_datasets_repos(),
        "huggingface": audit_hf_sources(),
        "kaggle": audit_kaggle_sources(),
        "local_historic": audit_local_historic(),
        "web_only": audit_web_only_sources(),
    }
    hub_dir().mkdir(parents=True, exist_ok=True)
    (hub_dir() / "audit_latest.json").write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
    return audit


def _parse_mrchartist_fpi_monthly(payload: dict[str, Any]) -> pd.DataFrame:
    months = payload.get("months") or []
    rows: list[dict[str, Any]] = []
    for item in months:
        if not isinstance(item, dict):
            continue
        month = str(item.get("month") or "").strip()
        parsed = pd.to_datetime(month, format="%d-%b-%Y", errors="coerce")
        if pd.isna(parsed):
            parsed = pd.to_datetime(month, errors="coerce", dayfirst=True)
        if pd.isna(parsed):
            continue
        row = {"date": parsed.strftime("%Y-%m-%d"), "source": "mrchartist_fpi_monthly"}
        for key in (
            "equity_gross_purchase",
            "equity_gross_sales",
            "equity_net",
            "debt_gross_purchase",
            "debt_gross_sales",
            "debt_net",
            "hybrid_gross_purchase",
            "hybrid_gross_sales",
            "hybrid_net",
            "total_net",
        ):
            if key in item:
                row[key] = pd.to_numeric(item.get(key), errors="coerce")
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def _parse_mrchartist_sector_history(payload: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for period_block in payload:
        if not isinstance(period_block, dict):
            continue
        period = str(period_block.get("period") or period_block.get("date_code") or "")
        for sector_row in period_block.get("sectors") or []:
            if not isinstance(sector_row, dict):
                continue
            row = {"period": period, "source": "mrchartist_sector_history"}
            row.update({k: sector_row.get(k) for k in sector_row})
            rows.append(row)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _ingest_vishalvx_all_indices(*, force_fetch: bool = False) -> dict[str, Any]:
    from trade_integrations.dataflows.external_financial_datasets.curated_ingest import hub_dir as curated_hub

    base = "https://raw.githubusercontent.com/vishalvx/nifty-indices-datasets/main/datasets"
    files = (
        "nifty50_weights.csv",
        "niftymidcap50_weights.csv",
        "niftysmallcap50_weights.csv",
        "nifty500momentum50_weights.csv",
    )
    results: dict[str, Any] = {}
    for fname in files:
        slug = fname.replace("_weights.csv", "")
        cache = curated_hub() / "cache" / "vishalvx" / fname
        url = f"{base}/{fname}"
        try:
            fetch_to_path(url, cache, force=force_fetch, timeout=120)
            wide = pd.read_csv(cache)
            wide = wide.rename(columns={"DATE": "date"})
            wide["date"] = pd.to_datetime(wide["date"], errors="coerce").dt.strftime("%Y-%m-%d")
            out = curated_hub() / "indices" / f"{slug}_weights_monthly_wide.parquet"
            _write_parquet(wide, out)
            results[slug] = {"status": "ok", "rows": len(wide), "path": str(out)}
        except Exception as exc:
            results[slug] = {"status": "error", "error": str(exc)}
    return results


def _ingest_mrchartist_extended(*, force_fetch: bool = False) -> dict[str, Any]:
    from trade_integrations.dataflows.external_financial_datasets.curated_ingest import (
        _fetch_mrchartist_github_static,
        hub_dir as curated_hub,
    )

    meta = _fetch_mrchartist_github_static(force_fetch=force_fetch)
    cache = curated_hub() / "cache" / "mrchartist_github"
    results: dict[str, Any] = {"github_static": meta, "datasets": {}}

    fpi_path = cache / "data_fpi_monthly_history.json"
    if fpi_path.is_file():
        payload = json.loads(fpi_path.read_text(encoding="utf-8"))
        monthly = _parse_mrchartist_fpi_monthly(payload)
        if not monthly.empty:
            out = curated_hub() / "flows" / "fpi_monthly_history.parquet"
            _write_parquet(monthly, out)
            results["datasets"]["fpi_monthly_history"] = {"rows": len(monthly), "path": str(out)}

    sector_path = cache / "data_sector_history.json"
    if sector_path.is_file():
        payload = json.loads(sector_path.read_text(encoding="utf-8"))
        sectors = _parse_mrchartist_sector_history(payload if isinstance(payload, list) else [])
        if not sectors.empty:
            out = curated_hub() / "flows" / "fpi_sector_history.parquet"
            _write_parquet(sectors, out)
            results["datasets"]["fpi_sector_history"] = {"rows": len(sectors), "path": str(out)}

    for extra in ("data_debt_utilisation.json", "data_country_auc.json", "data_odi_pn.json"):
        path = cache / extra
        if path.is_file():
            dest = curated_hub() / "flows" / extra.replace("data_", "")
            dest.write_bytes(path.read_bytes())
            results["datasets"][extra] = {"path": str(dest), "bytes": path.stat().st_size}

    return results


def _ingest_local_historic_gaps() -> dict[str, Any]:
    from trade_integrations.nse_browser.parsers.historic_data import (
        historic_data_dir,
        parse_fii_dii_trading_activity_csv,
        parse_india_cpi_monthly_csv,
        parse_nifty_fo_oi_daily_csv,
        parse_nifty50_fo_panel_csv,
    )
    from trade_integrations.nse_browser.repository import repo_root

    root = historic_data_dir(repo_root())
    results: dict[str, Any] = {}
    specs = (
        ("Fii Dii Trading activity.csv", "fii_dii_trading_activity", parse_fii_dii_trading_activity_csv),
        ("india_cpi_monthly_yoy.csv", "india_cpi_monthly_yoy", parse_india_cpi_monthly_csv),
        ("nifty_oi_ data.csv", "nifty_oi_daily", parse_nifty_fo_oi_daily_csv),
        ("nifty50_fo_data_filtered.csv", "nifty50_fo_panel", parse_nifty50_fo_panel_csv),
    )
    for filename, stem, parser in specs:
        path = root / filename
        if not path.is_file():
            results[stem] = {"status": "skipped", "reason": "missing_file"}
            continue
        try:
            frame = parser(path)
            if frame.empty:
                results[stem] = {"status": "skipped", "reason": "empty_parse"}
                continue
            out = hub_dir() / "local_historic" / f"{stem}.parquet"
            _write_parquet(frame, out)
            results[stem] = {"status": "ok", "rows": len(frame), "path": str(out)}
        except Exception as exc:
            results[stem] = {"status": "error", "error": str(exc)}

    # Re-run full historic folder ingest for anything else
    from trade_integrations.nse_browser.parsers.historic_data import ingest_historic_data_folder

    results["historic_data_folder"] = ingest_historic_data_folder(repo_root())
    return results


def _ingest_github_dataset_extras() -> dict[str, Any]:
    """Fetch any extra CSV files discovered in datasets/* repos."""
    extras = audit_github_datasets_repos()
    results: dict[str, Any] = {}
    for repo, info in extras.items():
        for rel in info.get("unconfigured_csv") or []:
            branch = next(str(s["branch"]) for s in DATASETS if s["repo"] == repo)
            key = f"{repo.replace('/', '_')}_{Path(rel).stem}"
            dest = hub_dir() / "raw" / key / Path(rel).name
            url = raw_url(repo, branch, rel)
            try:
                fetch_to_path(url, dest, force=False, timeout=120)
                results[key] = {"status": "ok", "path": str(dest), "source": url}
            except Exception as exc:
                results[key] = {"status": "error", "error": str(exc), "url": url}
    return results


def ingest_audit_gaps(*, force_fetch: bool = False) -> dict[str, Any]:
    """Download and parse files identified by audit as missing or extended."""
    from trade_integrations.dataflows.external_financial_datasets.curated_ingest import ingest_curated_market_data
    from trade_integrations.dataflows.external_financial_datasets.ingest import ingest_external_financial_datasets
    from trade_integrations.dataflows.github_datasets import ingest_github_macro_datasets

    results: dict[str, Any] = {
        "vishalvx_all_indices": _ingest_vishalvx_all_indices(force_fetch=force_fetch),
        "mrchartist_extended": _ingest_mrchartist_extended(force_fetch=force_fetch),
        "local_historic_gaps": _ingest_local_historic_gaps(),
        "github_datasets_extras": _ingest_github_dataset_extras(),
    }

    results["curated_market"] = ingest_curated_market_data(force_fetch=force_fetch, include_kaggle=True)
    results["github_macro"] = ingest_github_macro_datasets(force_fetch=force_fetch)
    results["external_financial"] = ingest_external_financial_datasets(
        force_fetch=force_fetch,
        skip_curated=True,
    )

    audit = run_full_source_audit()
    payload = {
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "ingest_results": results,
        "post_ingest_audit": audit,
    }
    (hub_dir() / "ingest_latest.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload
