"""Normalized hub datasets keyed by domain/market/symbol."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.data_router.types import FetchSpec
from trade_integrations.hub_storage.parquet_io import read_dataframe, upsert_by_keys, write_dataframe
from trade_integrations.tiered_api.cache_policy import should_cache_response

logger = logging.getLogger(__name__)

_DATASETS_REL = Path("_data") / "datasets"
_OHLCV_COLUMNS = ["date", "open", "high", "low", "close", "volume", "source"]


def _datasets_root() -> Path:
    root = get_hub_dir() / _DATASETS_REL
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ohlcv_path(market: str, symbol: str) -> Path:
    safe = symbol.strip().upper().replace("/", "_")
    return _datasets_root() / "ohlcv" / market.strip().lower() / f"{safe}.parquet"


def _manifest_path() -> Path:
    return _datasets_root() / "manifest.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ohlcv_frame(frame: pd.DataFrame, *, source: str) -> pd.DataFrame:
    """Standardize OHLCV columns for storage."""
    if frame.empty:
        return frame
    out = frame.copy()
    rename = {
        "Date": "date",
        "trade_date": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    for src, dst in rename.items():
        if src in out.columns and dst not in out.columns:
            out = out.rename(columns={src: dst})
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out["source"] = source
    cols = [c for c in _OHLCV_COLUMNS if c in out.columns]
    out = out[cols].dropna(subset=["date"], how="any")
    return out


def read_ohlcv(spec: FetchSpec) -> tuple[pd.DataFrame, str | None]:
    """Read normalized OHLCV; returns (frame, path) or empty."""
    if not spec.symbol:
        return pd.DataFrame(), None
    path = _ohlcv_path(spec.market, spec.symbol)
    if not path.is_file() and not path.with_suffix(".csv").is_file():
        legacy = (
            get_hub_dir()
            / "_data"
            / "capture"
            / spec.symbol.strip().upper()
            / "ohlcv_daily"
            / "bars.parquet"
        )
        if legacy.is_file():
            path = legacy
        else:
            return pd.DataFrame(), None
    frame = read_dataframe(path)
    if frame.empty:
        return frame, str(path)
    frame = normalize_ohlcv_frame(frame, source=str(frame.get("source", ["hub"])[0] if "source" in frame.columns else "hub"))
    if spec.start and spec.end and "date" in frame.columns:
        mask = (frame["date"] >= spec.start[:10]) & (frame["date"] <= spec.end[:10])
        frame = frame.loc[mask]
    if len(frame) >= 1:
        return frame, str(path)
    return pd.DataFrame(), str(path)


def write_ohlcv(
    spec: FetchSpec,
    frame: pd.DataFrame,
    *,
    source: str,
) -> str | None:
    """Upsert OHLCV rows; returns path or None if rejected."""
    if not spec.symbol or frame.empty:
        return None
    normalized = normalize_ohlcv_frame(frame, source=source)
    if normalized.empty:
        return None
    path = _ohlcv_path(spec.market, spec.symbol)
    existing = read_dataframe(path) if path.is_file() else pd.DataFrame()
    if not existing.empty:
        existing = normalize_ohlcv_frame(existing, source=str(existing.get("source", ["hub"])[0] if "source" in existing.columns else "hub"))
    merged = upsert_by_keys(existing, normalized, dedupe_keys=["date"], sort_key="date")
    write_dataframe(merged, path)
    _update_manifest("ohlcv", spec.market, spec.symbol, path, len(merged))
    return str(path)


def _relative_hub_path(path: Path) -> str:
    try:
        return str(path.relative_to(get_hub_dir()))
    except ValueError:
        return str(path)


def _update_manifest(domain: str, market: str, symbol: str, path: Path, rows: int) -> None:
    manifest_path = _manifest_path()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"entries": []}
    except (OSError, json.JSONDecodeError):
        manifest = {"entries": []}
    key = f"{domain}:{market}:{symbol.upper()}"
    entries = [e for e in manifest.get("entries", []) if e.get("key") != key]
    entries.append(
        {
            "key": key,
            "domain": domain,
            "market": market,
            "symbol": symbol.upper(),
            "path": _relative_hub_path(path),
            "rows": rows,
            "updated_at": _now_iso(),
        }
    )
    manifest["entries"] = entries[-5000:]
    manifest["updated_at"] = _now_iso()
    try:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.debug("datasets manifest write failed: %s", exc)


def read(spec: FetchSpec) -> tuple[Any, str | None, bool]:
    """Domain-aware read. Returns (data, path, cache_hit)."""
    if spec.domain == "ohlcv":
        frame, path = read_ohlcv(spec)
        hit = not frame.empty
        return frame, path, hit
    return None, None, False


def write(spec: FetchSpec, data: Any, *, source: str) -> str | None:
    if spec.domain == "ohlcv" and isinstance(data, pd.DataFrame):
        return write_ohlcv(spec, data, source=source)
    if spec.domain == "flows" and isinstance(data, pd.DataFrame):
        return write_flows(spec, data, source=source)
    return None


def _flows_path(dataset_id: str) -> Path:
    safe = dataset_id.strip().lower().replace("/", "_")
    return _datasets_root() / "flows" / f"{safe}.parquet"


def write_flows(
    spec: FetchSpec,
    frame: pd.DataFrame,
    *,
    source: str,
) -> str | None:
    dataset_id = str(spec.extra.get("dataset_id") or spec.symbol or "flows")
    if frame.empty:
        return None
    path = _flows_path(dataset_id)
    out = frame.copy()
    if "source" not in out.columns:
        out["source"] = source
    existing = read_dataframe(path) if path.is_file() else pd.DataFrame()
    dedupe_keys = ["date"] if "date" in out.columns else list(out.columns[:1])
    merged = upsert_by_keys(existing, out, dedupe_keys=dedupe_keys, sort_key=dedupe_keys[0] if dedupe_keys else None)
    write_dataframe(merged, path)
    _update_manifest("flows", spec.market, dataset_id, path, len(merged))
    return str(path)
