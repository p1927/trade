"""Cold-tier historical parquet storage under reports/hub/_data/history/."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.hub_storage.parquet_io import concat_dataframes

from trade_integrations.context.hub import get_hub_dir

_HISTORY_SUBDIR = "_data/history"
_PANEL_SUBDIR = "_data/index_factors/panel"
_HOT_RETENTION_DAYS = 365
_PANEL_MANIFEST = "panel_manifest.json"


def get_history_dir() -> Path:
    return get_hub_dir() / _HISTORY_SUBDIR


def get_panel_dir() -> Path:
    return get_hub_dir() / _PANEL_SUBDIR


def history_path(name: str) -> Path:
    stem = name.removesuffix(".parquet")
    return get_history_dir() / f"{stem}.parquet"


def panel_path(name: str) -> Path:
    stem = name.removesuffix(".parquet")
    return get_panel_dir() / f"{stem}.parquet"


def panel_staging_path(name: str) -> Path:
    stem = name.removesuffix(".parquet")
    return get_panel_dir() / f"{stem}.staging.parquet"


def panel_previous_path(name: str) -> Path:
    stem = name.removesuffix(".parquet")
    return get_panel_dir() / f"{stem}.previous.parquet"


def panel_manifest_path() -> Path:
    return get_panel_dir() / _PANEL_MANIFEST


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        import fcntl

        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    frame.to_parquet(path, index=False)
                except ImportError:
                    frame.to_csv(path.with_suffix(".csv"), index=False)
                csv_path = path.with_suffix(".csv")
                if not csv_path.is_file():
                    frame.to_csv(csv_path, index=False)
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        try:
            frame.to_parquet(path, index=False)
        except ImportError:
            frame.to_csv(path.with_suffix(".csv"), index=False)
        csv_path = path.with_suffix(".csv")
        if not csv_path.is_file():
            frame.to_csv(csv_path, index=False)


def _read_parquet(path: Path) -> pd.DataFrame:
    csv_path = path.with_suffix(".csv")
    if path.is_file():
        try:
            return pd.read_parquet(path)
        except Exception:
            if csv_path.is_file():
                return pd.read_csv(csv_path)
            return pd.DataFrame()
    if csv_path.is_file():
        return pd.read_csv(csv_path)
    return pd.DataFrame()


def _preserve_full_datetime(name: str) -> bool:
    return name.startswith("nifty50_intraday_")


def _normalize_date_column(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if _preserve_full_datetime(name):
        out["date"] = out["date"].astype(str)
    else:
        out["date"] = out["date"].astype(str).str[:10]
    return out


def _dedupe_keys(name: str, frame: pd.DataFrame) -> list[str]:
    if name == "sector_index_daily" and "index_slug" in frame.columns:
        return ["date", "index_slug"]
    if name.endswith("_constituent_ohlcv_daily") and "symbol" in frame.columns:
        keys = ["date", "symbol"]
        if "index_slug" in frame.columns:
            keys.append("index_slug")
        return keys
    if name.startswith("nifty50_intraday_") and "interval" in frame.columns:
        return ["date", "interval"]
    if name in {"flow_cash_daily", "flow_derivatives_daily"}:
        return ["date"]
    if name == "nifty50_constituents_membership_summary" and "symbol" in frame.columns:
        return ["symbol"]
    if "granularity" in frame.columns:
        return ["date", "granularity"]
    return ["date"]


def load_history_dataset(name: str) -> pd.DataFrame:
    """Load a cold-tier dataset by stem name (e.g. ``macro_daily``)."""
    frame = _read_parquet(history_path(name))
    if frame.empty or "date" not in frame.columns:
        return frame
    out = _normalize_date_column(name, frame)
    keys = _dedupe_keys(name, out)
    return out.sort_values(keys).drop_duplicates(keys, keep="last").reset_index(drop=True)


def save_history_dataset(name: str, frame: pd.DataFrame, *, merge: bool | None = None) -> dict[str, Any]:
    """Persist a cold-tier dataset and update manifest.

    When ``merge=True`` (default for real-time datasets via ingest_policy), new rows
    are merged with the existing cold tier on dedupe keys — no full replace bloat.
    """
    if frame.empty:
        return {"status": "skipped", "reason": "empty_frame", "dataset": name}
    out = frame.copy()
    if "date" not in out.columns:
        return {"status": "error", "reason": "missing_date_column", "dataset": name}
    out = _normalize_date_column(name, out)
    keys = _dedupe_keys(name, out)
    if merge is None:
        try:
            from trade_integrations.dataflows.ingest_policy import merge_on_save_default

            merge = merge_on_save_default(name)
        except ImportError:
            merge = False
    if merge:
        existing = load_history_dataset(name)
        if not existing.empty:
            if "source" in out.columns or "source" in existing.columns:
                from trade_integrations.dataflows.index_research.history_ingest import merge_with_priority

                out = merge_with_priority([existing, out], on=keys)
            else:
                out = concat_dataframes(existing, out)
    out = out.sort_values(keys).drop_duplicates(keys, keep="last").reset_index(drop=True)
    path = history_path(name)
    _write_parquet(out, path)
    meta = _update_manifest(name, out, path)
    return {"status": "ok", "dataset": name, "rows": len(out), **meta}


def load_panel(name: str = "NIFTY_2006_present") -> pd.DataFrame:
    frame = _read_parquet(panel_path(name))
    if frame.empty or "date" not in frame.columns:
        return frame
    out = frame.copy()
    out["date"] = out["date"].astype(str).str[:10]
    return out.sort_values("date").reset_index(drop=True)


def _update_panel_manifest(name: str, frame: pd.DataFrame, path: Path, *, invariant_report: dict[str, Any]) -> None:
    manifest_path = panel_manifest_path()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    panels: dict[str, Any] = dict(payload.get("panels") or {})
    from trade_integrations.dataflows.index_research.panel_invariants import factor_stats_snapshot

    panels[name] = {
        "path": str(path.relative_to(get_hub_dir())),
        "rows": len(frame),
        "columns": len(frame.columns),
        "date_range": {
            "start": str(frame["date"].iloc[0]),
            "end": str(frame["date"].iloc[-1]),
        },
        "sha256": _sha256_file(path) if path.is_file() else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "invariants": invariant_report,
        "factor_stats": factor_stats_snapshot(frame),
    }
    payload["panels"] = panels
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def save_panel(
    frame: pd.DataFrame,
    name: str = "NIFTY_2006_present",
    *,
    force: bool = False,
    skip_invariants: bool = False,
) -> dict[str, Any]:
    """Write-Audit-Publish: stage → invariant check → promote production panel."""
    if frame.empty:
        return {"status": "skipped", "reason": "empty_frame", "panel": name}

    out = frame.copy()
    out["date"] = out["date"].astype(str).str[:10]
    out = out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)

    prod_path = panel_path(name)
    staging_path = panel_staging_path(name)
    previous_path = panel_previous_path(name)
    existing = load_panel(name) if prod_path.is_file() else pd.DataFrame()

    invariant_report: dict[str, Any] = {"ok": True, "skipped": skip_invariants}
    if not skip_invariants:
        from trade_integrations.dataflows.index_research.panel_invariants import assert_panel_invariants

        force_save = force or os.getenv("INDEX_PANEL_SAVE_FORCE", "").strip().lower() in {"1", "true", "yes"}
        invariant_report = assert_panel_invariants(
            out,
            existing_panel=existing if not existing.empty else None,
            force=force_save,
        )

    _write_parquet(out, staging_path)
    _write_parquet(out, staging_path.with_suffix(".csv")) if not staging_path.is_file() else None

    if prod_path.is_file():
        shutil.copy2(prod_path, previous_path)
        prev_csv = prod_path.with_suffix(".csv")
        if prev_csv.is_file():
            shutil.copy2(prev_csv, previous_path.with_suffix(".csv"))

    shutil.copy2(staging_path, prod_path)
    staging_csv = staging_path.with_suffix(".csv")
    prod_csv = prod_path.with_suffix(".csv")
    if staging_csv.is_file():
        shutil.copy2(staging_csv, prod_csv)

    _update_panel_manifest(name, out, prod_path, invariant_report=invariant_report)

    try:
        staging_path.unlink(missing_ok=True)
        staging_path.with_suffix(".csv").unlink(missing_ok=True)
    except OSError:
        pass

    return {
        "status": "ok",
        "panel": name,
        "rows": len(out),
        "start": str(out["date"].iloc[0]),
        "end": str(out["date"].iloc[-1]),
        "columns": len(out.columns),
        "invariants_ok": invariant_report.get("ok", True),
        "forced": invariant_report.get("forced", False),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _update_manifest(name: str, frame: pd.DataFrame, path: Path) -> dict[str, str]:
    manifest_path = get_history_dir() / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    files: list[dict[str, Any]] = list(payload.get("files") or [])
    files = [entry for entry in files if entry.get("dataset") != name]
    entry = {
        "dataset": name,
        "path": str(path.relative_to(get_hub_dir())),
        "rows": len(frame),
        "date_range": {
            "start": str(frame["date"].iloc[0]),
            "end": str(frame["date"].iloc[-1]),
        },
        "sha256": _sha256_file(path) if path.is_file() else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    files.append(entry)
    payload["files"] = sorted(files, key=lambda row: row.get("dataset", ""))
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"start": entry["date_range"]["start"], "end": entry["date_range"]["end"]}


def hot_retention_days() -> int:
    return _HOT_RETENTION_DAYS
