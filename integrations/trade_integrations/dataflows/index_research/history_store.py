"""Cold-tier historical parquet storage under reports/hub/_data/history/."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir

_HISTORY_SUBDIR = "_data/history"
_PANEL_SUBDIR = "_data/index_factors/panel"
_HOT_RETENTION_DAYS = 365


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
            out = pd.concat([existing, out], ignore_index=True)
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


def save_panel(frame: pd.DataFrame, name: str = "NIFTY_2006_present") -> dict[str, Any]:
    if frame.empty:
        return {"status": "skipped", "reason": "empty_frame", "panel": name}
    out = frame.copy()
    out["date"] = out["date"].astype(str).str[:10]
    out = out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    path = panel_path(name)
    _write_parquet(out, path)
    return {
        "status": "ok",
        "panel": name,
        "rows": len(out),
        "start": str(out["date"].iloc[0]),
        "end": str(out["date"].iloc[-1]),
        "columns": len(out.columns),
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
