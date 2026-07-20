"""Repo-local NSE data archive under data/nse/ (parquet + manifest, git-tracked)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.nse_browser.hub_writer import _read_parquet_or_csv, _write_parquet, upsert_daily_parquet
from trade_integrations.nse_browser.registry import DATASETS, get_dataset, hub_root

logger = logging.getLogger(__name__)

_MANIFEST_NAME = "manifest.json"


def _trade_stack_root() -> Path:
    if custom := os.environ.get("TRADE_STACK_ROOT", "").strip():
        return Path(custom).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


def repo_root() -> Path:
    if custom := os.environ.get("NSE_DATA_REPO", "").strip():
        path = Path(custom).expanduser()
        if not path.is_absolute():
            path = _trade_stack_root() / path
        return path.resolve()
    return _trade_stack_root() / "data" / "nse"


def raw_dir(dataset: str) -> Path:
    return repo_root() / "raw" / dataset


def repo_parquet_path(dataset_id: str) -> Path | None:
    spec = get_dataset(dataset_id)
    if spec is None:
        return None
    if spec.id in ("bulk_deals", "delivery", "pe_pb"):
        return repo_root() / "archives" / f"{spec.id}.parquet"
    return repo_root() / spec.id / Path(spec.parquet_rel).name


def manifest_path() -> Path:
    return repo_root() / _MANIFEST_NAME


def _load_manifest() -> dict[str, Any]:
    path = manifest_path()
    if not path.is_file():
        return {"files": [], "updated_at": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload.setdefault("files", [])
            return payload
    except Exception as exc:
        logger.debug("manifest read failed: %s", exc)
    return {"files": [], "updated_at": None}


def _save_manifest(body: dict[str, Any]) -> None:
    repo_root().mkdir(parents=True, exist_ok=True)
    body["updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path().write_text(json.dumps(body, indent=2), encoding="utf-8")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _date_range(frame: pd.DataFrame, date_col: str = "date") -> dict[str, str | None]:
    if frame.empty or date_col not in frame.columns:
        return {"start": None, "end": None}
    dates = frame[date_col].astype(str).str[:10]
    return {"start": str(dates.min()), "end": str(dates.max())}


def save_raw_file(
    content: bytes | str,
    *,
    dataset: str,
    name: str,
) -> Path:
    """Save original download to data/nse/raw/{dataset}/ (gitignored)."""
    dest_dir = raw_dir(dataset)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    if isinstance(content, bytes):
        dest.write_bytes(content)
    else:
        dest.write_text(content, encoding="utf-8")
    return dest


def upsert_repo_parquet(
    frame: pd.DataFrame,
    *,
    dataset_id: str,
    source: str = "nse_browser",
    date_col: str = "date",
) -> int:
    """Merge frame into repo parquet and update manifest."""
    if frame.empty:
        return 0
    path = repo_parquet_path(dataset_id)
    if path is None:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = upsert_daily_parquet(frame, path=path, date_col=date_col)

    merged = _read_parquet_or_csv(path)
    rel = str(path.relative_to(repo_root()))
    entry = {
        "path": rel,
        "dataset": dataset_id,
        "sha256": _sha256_file(path) if path.is_file() else "",
        "rows": len(merged),
        "date_range": _date_range(merged, date_col),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }
    manifest = _load_manifest()
    files = [f for f in manifest.get("files", []) if f.get("path") != rel]
    files.append(entry)
    manifest["files"] = files
    _save_manifest(manifest)
    return rows


def load_repo_dataset(dataset_id: str) -> pd.DataFrame:
    path = repo_parquet_path(dataset_id)
    if path is None or not path.is_file():
        return pd.DataFrame()
    frame = _read_parquet_or_csv(path)
    if frame.empty:
        return frame
    if "date" in frame.columns:
        frame = frame.copy()
        frame["date"] = frame["date"].astype(str).str[:10]
        if "granularity" in frame.columns:
            frame["granularity"] = frame["granularity"].fillna("daily").astype(str)
            return frame.sort_values("date").drop_duplicates(["date", "granularity"], keep="last")
        return frame.sort_values("date").drop_duplicates("date", keep="last")
    return frame


def ingest_repository_to_hub(*, allow_live_fetch: bool = True, enrich_days: int = 365) -> dict[str, int]:
    """Sync repo parquet files into reports/hub/_data/nse_browser/."""
    sync_all_repo_seed_layers(allow_live_fetch=allow_live_fetch, enrich_days=enrich_days)
    counts: dict[str, int] = {}
    hub = hub_root()
    hub.mkdir(parents=True, exist_ok=True)

    for dataset_id, spec in DATASETS.items():
        repo_frame = load_repo_dataset(dataset_id)
        if repo_frame.empty:
            continue
        if spec.id in ("bulk_deals", "delivery", "pe_pb"):
            hub_path = hub / spec.parquet_rel
        else:
            hub_path = hub / Path(spec.parquet_rel).name
        hub_path.parent.mkdir(parents=True, exist_ok=True)
        n = upsert_daily_parquet(repo_frame, path=hub_path, date_col=spec.date_col)
        counts[dataset_id] = n
        logger.debug("Ingested %s rows for %s -> %s", n, dataset_id, hub_path)

    return counts


def load_nse_repository_fii_dii_frame(start: str, end: str) -> pd.DataFrame:
    """Load FII/DII from repo parquet for backfill merge."""
    frame = load_repo_dataset("fii_dii")
    if frame.empty or "date" not in frame.columns:
        return pd.DataFrame()
    out = frame.copy()
    out["date"] = out["date"].astype(str).str[:10]
    out = out[(out["date"] >= start[:10]) & (out["date"] <= end[:10])]
    if "granularity" in out.columns:
        out = out[out["granularity"].astype(str) != "monthly"]
    if not out.empty and "source" not in out.columns:
        out["source"] = "nse_repository"
    return out.reset_index(drop=True)


def seed_mrchartist_fii_dii(*, allow_live_fetch: bool = True) -> int:
    """Bootstrap repo FII/DII from Mr. Chartist history-full (gap filler)."""
    from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
        fetch_mrchartist_flow_frame,
    )

    frame = fetch_mrchartist_flow_frame(include_seeded=False, allow_live_fetch=allow_live_fetch)
    if frame.empty:
        return 0
    cols = [c for c in ("date", "fii_net", "dii_net", "nifty_pcr", "source") if c in frame.columns]
    slim = frame[cols].copy() if cols else frame.copy()
    if "source" not in slim.columns:
        slim["source"] = "mrchartist"
    return upsert_repo_parquet(slim, dataset_id="fii_dii", source="mrchartist")


def monthly_cash_csv_path() -> Path:
    return repo_root() / "fii_dii" / "fii_dii_monthly_cash.csv"


def load_monthly_cash_fii_dii_frame() -> pd.DataFrame:
    """Load NSE monthly cash FII/DII from git-tracked CSV seed."""
    from trade_integrations.nse_browser.parsers.fii_dii import parse_fii_dii_monthly_cash_csv

    path = monthly_cash_csv_path()
    if not path.is_file():
        return pd.DataFrame()
    return parse_fii_dii_monthly_cash_csv(path.read_text(encoding="utf-8"))


def historic_fii_dii_trading_activity_path() -> Path:
    from trade_integrations.nse_browser.parsers.historic_data import historic_data_dir

    return historic_data_dir(repo_root()) / "Fii Dii Trading activity.csv"


def seed_historic_fii_dii_trading_activity() -> int:
    """Merge historic_data daily FII/DII cash CSV into repo fii_dii."""
    from trade_integrations.nse_browser.parsers.fii_dii import (
        merge_fii_dii_variants,
        parse_fii_dii_trading_activity_csv,
    )

    path = historic_fii_dii_trading_activity_path()
    if not path.is_file():
        return 0
    frame = parse_fii_dii_trading_activity_csv(path.read_text(encoding="utf-8"))
    if frame.empty:
        return 0
    existing = load_repo_dataset("fii_dii")
    merged = merge_fii_dii_variants(existing, frame)
    return upsert_repo_parquet(merged, dataset_id="fii_dii", source="historic_data_fii_dii")


def nifty50_fo_filtered_path() -> Path:
    from trade_integrations.nse_browser.parsers.historic_data import historic_data_dir

    return historic_data_dir(repo_root()) / "nifty50_fo_data_filtered.csv"


def seed_historic_nifty50_fo_derivatives() -> int:
    """Merge Nifty 50 stock F&O bhavcopy PCR / F&O proxies into repo fii_dii."""
    from trade_integrations.nse_browser.parsers.fii_dii import overlay_derivative_columns
    from trade_integrations.nse_browser.parsers.fo_derivatives import parse_nifty50_fo_bhavcopy_csv

    path = nifty50_fo_filtered_path()
    if not path.is_file():
        return 0
    frame = parse_nifty50_fo_bhavcopy_csv(path)
    if frame.empty:
        return 0
    existing = load_repo_dataset("fii_dii")
    merged = overlay_derivative_columns(existing, frame)
    return upsert_repo_parquet(merged, dataset_id="fii_dii", source="historic_data_nifty50_fo")


def seed_aeron7_nifty_futures_derivatives() -> int:
    """Merge Aeron7 NIFTY F1/F2 futures volume proxies when a local clone exists."""
    from trade_integrations.nse_browser.parsers.aeron7_intraday import (
        aggregate_aeron7_nifty_futures,
        aeron7_intraday_roots,
    )
    from trade_integrations.nse_browser.parsers.fii_dii import overlay_derivative_columns
    from trade_integrations.nse_browser.parsers.historic_data import historic_data_dir

    roots = aeron7_intraday_roots(historic_data_dir(repo_root()))
    if not roots:
        return 0

    frames = [aggregate_aeron7_nifty_futures(root) for root in roots]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return 0

    import pandas as pd

    overlay = pd.concat(frames, ignore_index=True)
    overlay = overlay.sort_values("date").drop_duplicates("date", keep="last")
    existing = load_repo_dataset("fii_dii")
    merged = overlay_derivative_columns(existing, overlay)
    return upsert_repo_parquet(merged, dataset_id="fii_dii", source="historic_data_aeron7_futures")


def seed_nse_monthly_cash_fii_dii() -> int:
    """Merge monthly NSE cash FII/DII seed into repo (month-end dates)."""
    from trade_integrations.nse_browser.parsers.fii_dii import merge_fii_dii_variants

    monthly = load_monthly_cash_fii_dii_frame()
    if monthly.empty:
        return 0
    save_raw_file(
        monthly_cash_csv_path().read_text(encoding="utf-8"),
        dataset="fii_dii",
        name="monthly_cash_seed.csv",
    )
    existing = load_repo_dataset("fii_dii")
    merged = merge_fii_dii_variants(existing, monthly)
    return upsert_repo_parquet(merged, dataset_id="fii_dii", source="nse_monthly_cash")


def sync_fii_dii_repo_layers() -> dict[str, int]:
    """Apply all repo seed layers: historic CSV → monthly cash → Mr Chartist gap fill."""
    counts: dict[str, int] = {}
    counts["historic_trading_activity"] = seed_historic_fii_dii_trading_activity()
    counts["historic_nifty50_fo"] = seed_historic_nifty50_fo_derivatives()
    counts["aeron7_nifty_futures"] = seed_aeron7_nifty_futures_derivatives()
    counts["monthly_cash"] = seed_nse_monthly_cash_fii_dii()
    existing = load_repo_dataset("fii_dii")
    if existing.empty:
        daily_rows = 0
    elif "granularity" in existing.columns:
        daily_rows = int((existing["granularity"].astype(str) != "monthly").sum())
    else:
        daily_rows = len(existing)
    if daily_rows < int(os.environ.get("NSE_FII_DII_MIN_HISTORY_DAYS", "100")):
        counts["mrchartist"] = seed_mrchartist_fii_dii()
    return counts


def mf_sebi_monthly_csv_path() -> Path:
    return repo_root() / "mf_sebi" / "mf_sebi_monthly_seed.csv"


def fii_sebi_monthly_csv_path() -> Path:
    return repo_root() / "fii_sebi" / "fii_sebi_monthly_seed.csv"


def load_mf_sebi_monthly_frame() -> pd.DataFrame:
    from trade_integrations.nse_browser.parsers.sebi_monthly import parse_mf_sebi_monthly_csv

    path = mf_sebi_monthly_csv_path()
    if not path.is_file():
        return pd.DataFrame()
    return parse_mf_sebi_monthly_csv(path.read_text(encoding="utf-8"))


def load_fii_sebi_monthly_frame() -> pd.DataFrame:
    from trade_integrations.nse_browser.parsers.sebi_monthly import parse_fii_sebi_monthly_csv

    path = fii_sebi_monthly_csv_path()
    if not path.is_file():
        return pd.DataFrame()
    return parse_fii_sebi_monthly_csv(path.read_text(encoding="utf-8"))


def seed_nse_mf_sebi_monthly() -> int:
    """Merge MF SEBI monthly seed into repo."""
    monthly = load_mf_sebi_monthly_frame()
    if monthly.empty:
        return 0
    save_raw_file(
        mf_sebi_monthly_csv_path().read_text(encoding="utf-8"),
        dataset="mf_sebi",
        name="monthly_seed.csv",
    )
    existing = load_repo_dataset("mf_sebi")
    merged = pd.concat([existing, monthly], ignore_index=True) if not existing.empty else monthly
    if "date" in merged.columns:
        dedupe = ["date", "granularity"] if "granularity" in merged.columns else ["date"]
        merged = merged.sort_values(dedupe).drop_duplicates(dedupe, keep="last")
    return upsert_repo_parquet(merged, dataset_id="mf_sebi", source="nse_mf_sebi_monthly")


def seed_nse_fii_sebi_monthly() -> int:
    """Merge FII SEBI monthly seed into repo."""
    monthly = load_fii_sebi_monthly_frame()
    if monthly.empty:
        return 0
    save_raw_file(
        fii_sebi_monthly_csv_path().read_text(encoding="utf-8"),
        dataset="fii_sebi",
        name="monthly_seed.csv",
    )
    existing = load_repo_dataset("fii_sebi")
    merged = pd.concat([existing, monthly], ignore_index=True) if not existing.empty else monthly
    if "date" in merged.columns:
        dedupe = ["date", "granularity"] if "granularity" in merged.columns else ["date"]
        merged = merged.sort_values(dedupe).drop_duplicates(dedupe, keep="last")
    return upsert_repo_parquet(merged, dataset_id="fii_sebi", source="nse_fii_sebi_monthly")


def sync_sebi_monthly_repo_layers() -> dict[str, int]:
    """Apply MF and FII SEBI monthly CSV seeds."""
    return {
        "mf_sebi": seed_nse_mf_sebi_monthly(),
        "fii_sebi": seed_nse_fii_sebi_monthly(),
    }


def sync_niftyinvest_api_flow(*, days: int = 365) -> dict[str, Any]:
    """Fetch recent Nifty Invest public API months into fii_dii repo."""
    from trade_integrations.dataflows.index_research.sources.web_flow_fetch import (
        seed_niftyinvest_flow_to_repo,
    )

    return seed_niftyinvest_flow_to_repo(days=days)


def sync_all_repo_seed_layers(*, allow_live_fetch: bool = True, enrich_days: int = 365) -> dict[str, int]:
    """Apply all git-tracked CSV seed layers into repo parquet."""
    from trade_integrations.nse_browser.parsers.historic_data import ingest_historic_data_folder

    counts: dict[str, int] = {}
    historic = ingest_historic_data_folder(repo_root())
    if historic.get("status") == "ok":
        counts["historic_data"] = int(historic.get("rows") or 0)
        for name, meta in (historic.get("datasets") or {}).items():
            if isinstance(meta, dict):
                row_count = int(meta.get("rows") or meta.get("months") or meta.get("membership_rows") or 0)
                if row_count:
                    counts[f"historic_{name}"] = row_count
    counts.update(sync_fii_dii_repo_layers())
    counts.update(sync_sebi_monthly_repo_layers())
    counts["sector_indices"] = seed_sector_indices_from_nifty50()
    if allow_live_fetch:
        ni = sync_niftyinvest_api_flow(days=enrich_days)
        if isinstance(ni, dict) and ni.get("status") == "ok":
            counts["niftyinvest_api"] = int(ni.get("rows") or 0)
        try:
            from trade_integrations.dataflows.index_research.sources.nselib_fetch import (
                backfill_nifty50_ohlcv_gaps,
            )

            ohlcv = backfill_nifty50_ohlcv_gaps(repo_root(), allow_live_fetch=True)
            if ohlcv.get("status") == "ok":
                counts["nselib_nifty50_ohlcv"] = int(ohlcv.get("rows_added") or 0)
        except Exception as exc:
            logger.debug("nselib nifty50 ohlcv gap fill skipped: %s", exc)
    return counts


def sector_indices_parquet_path() -> Path:
    return repo_root() / "sector_indices" / "sector_index_daily.parquet"


def load_sector_indices_frame(start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Load sector index OHLC from repo parquet."""
    path = sector_indices_parquet_path()
    if not path.is_file():
        return pd.DataFrame()
    frame = _read_parquet_or_csv(path)
    if frame.empty or "date" not in frame.columns:
        return frame
    out = frame.copy()
    out["date"] = out["date"].astype(str).str[:10]
    if start:
        out = out[out["date"] >= start[:10]]
    if end:
        out = out[out["date"] <= end[:10]]
    return out.sort_values(["date", "index_slug"]).reset_index(drop=True)


def seed_sector_indices_from_nifty50() -> int:
    """Parse data/nse/nifty50/*.csv into sector_index_daily.parquet."""
    from trade_integrations.nse_browser.parsers.sector_indices import load_nifty50_sector_csvs

    frame = load_nifty50_sector_csvs(repo_root())
    if frame.empty:
        return 0
    path = sector_indices_parquet_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = frame.sort_values(["date", "index_slug"]).drop_duplicates(["date", "index_slug"], keep="last")
    _write_parquet(merged, path)
    rows = len(merged)

    rel = str(path.relative_to(repo_root()))
    entry = {
        "path": rel,
        "dataset": "sector_indices",
        "sha256": _sha256_file(path) if path.is_file() else "",
        "rows": len(merged),
        "date_range": _date_range(merged, "date"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "nse_sector_csv",
    }
    manifest = _load_manifest()
    files = [f for f in manifest.get("files", []) if f.get("path") != rel]
    files.append(entry)
    manifest["files"] = files
    _save_manifest(manifest)
    return rows
