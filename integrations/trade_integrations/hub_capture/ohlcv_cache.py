"""Date-keyed hub cache for India daily OHLCV (OpenAlgo/INDstocks write-through)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.hub_capture.channel import record_channel_stat
from trade_integrations.hub_capture.registry import capture_base_dir
from trade_integrations.hub_storage.parquet_io import read_dataframe, write_dataframe

logger = logging.getLogger(__name__)

OHLCV_SERIES = "ohlcv_daily"
_META_NAME = "_meta.json"


def _datasets_redirect_enabled() -> bool:
    import os

    return os.getenv("DATA_ROUTER_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entity_id(symbol: str) -> str:
    raw = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
    aliases = {"^NSEI": "NIFTY", "NIFTY50": "NIFTY", "^INDIAVIX": "INDIAVIX"}
    return aliases.get(raw, raw)


def _series_dir(entity_id: str) -> Path:
    return capture_base_dir(entity_id) / OHLCV_SERIES


def _bars_path(entity_id: str) -> Path:
    return _series_dir(entity_id) / "bars.parquet"


def _meta_path(entity_id: str) -> Path:
    return _series_dir(entity_id) / _META_NAME


def _read_meta(entity_id: str) -> dict[str, Any]:
    path = _meta_path(entity_id)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_meta(entity_id: str, meta: dict[str, Any]) -> None:
    path = _meta_path(entity_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def _normalize_bars(frame: pd.DataFrame, *, source: str, vendor: str) -> pd.DataFrame:
    if frame.empty or "date" not in frame.columns or "close" not in frame.columns:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "source", "vendor", "cached_at"])

    out = frame.copy()
    out["date"] = out["date"].astype(str).str[:10]
    out = out.dropna(subset=["date", "close"])
    out["source"] = source
    out["vendor"] = vendor
    out["cached_at"] = _now_iso()
    cols = ["date", "open", "high", "low", "close", "volume", "source", "vendor", "cached_at"]
    for col in cols:
        if col not in out.columns:
            out[col] = None
    return out[cols].drop_duplicates(subset=["date"], keep="last").sort_values("date")


def read_cached_bars(
    symbol: str,
    start_date: str,
    end_date: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load cached OHLCV bars for ``[start_date, end_date]`` inclusive."""
    entity = _entity_id(symbol)
    if _datasets_redirect_enabled():
        try:
            from trade_integrations.data_router.normalized_store import read_ohlcv
            from trade_integrations.data_router.types import FetchSpec

            spec = FetchSpec(
                domain="ohlcv",
                market="india_equity",
                symbol=symbol,
                start=start_date,
                end=end_date,
            )
            frame, path = read_ohlcv(spec)
            if not frame.empty:
                cached_dates = frame["date"].astype(str).tolist() if "date" in frame.columns else []
                return frame, {
                    "entity_id": entity,
                    "cache_hit": True,
                    "cached_dates": cached_dates,
                    "cache_as_of": None,
                    "source": "datasets",
                    "normalized_path": path,
                }
        except Exception as exc:
            logger.debug("datasets redirect read miss: %s", exc)

    path = _bars_path(entity)
    meta = _read_meta(entity)
    if not path.is_file():
        return pd.DataFrame(columns=["date", "close"]), {
            "entity_id": entity,
            "cache_hit": False,
            "cached_dates": [],
            "missing_dates": [],
            "source": "hub_cache",
        }

    frame = read_dataframe(path)
    if frame.empty or "date" not in frame.columns:
        return pd.DataFrame(columns=["date", "close"]), {
            "entity_id": entity,
            "cache_hit": False,
            "cached_dates": [],
            "missing_dates": [],
            "source": "hub_cache",
        }

    frame = frame.copy()
    frame["date"] = frame["date"].astype(str).str[:10]
    mask = (frame["date"] >= start_date[:10]) & (frame["date"] <= end_date[:10])
    subset = frame.loc[mask].sort_values("date").reset_index(drop=True)
    cached_dates = subset["date"].tolist()
    if cached_dates:
        record_channel_stat("hub_hit", "ohlcv_daily")

    return subset, {
        "entity_id": entity,
        "cache_hit": bool(cached_dates),
        "cached_dates": cached_dates,
        "cache_as_of": meta.get("updated_at"),
        "source": "hub_cache",
    }


def write_cached_bars(
    symbol: str,
    bars: pd.DataFrame,
    *,
    source: str,
    vendor: str = "openalgo",
) -> dict[str, Any]:
    """Merge daily bars into the hub cache (keyed by bar ``date``)."""
    entity = _entity_id(symbol)
    normalized = _normalize_bars(bars, source=source, vendor=vendor)
    if normalized.empty:
        return {"status": "empty", "entity_id": entity}

    path = _bars_path(entity)
    existing = read_dataframe(path)
    if existing.empty:
        merged = normalized
    else:
        existing["date"] = existing["date"].astype(str).str[:10]
        merged = pd.concat([existing, normalized], ignore_index=True)
        merged = merged.drop_duplicates(subset=["date"], keep="last").sort_values("date")

    write_dataframe(merged, path)
    meta = {
        "entity_id": entity,
        "updated_at": _now_iso(),
        "bar_count": int(len(merged)),
        "first_date": str(merged["date"].iloc[0]) if len(merged) else None,
        "last_date": str(merged["date"].iloc[-1]) if len(merged) else None,
        "last_vendor": vendor,
        "last_source": source,
    }
    _write_meta(entity, meta)
    if _datasets_redirect_enabled():
        try:
            from trade_integrations.data_router.normalized_store import write_ohlcv
            from trade_integrations.data_router.types import FetchSpec

            write_ohlcv(
                FetchSpec(
                    domain="ohlcv",
                    market="india_equity",
                    symbol=symbol,
                ),
                merged,
                source=source,
            )
        except Exception as exc:
            logger.debug("datasets redirect write failed: %s", exc)
    return {"status": "ok", "entity_id": entity, "appended": int(len(normalized)), **meta}


def merge_with_cache(
    symbol: str,
    start_date: str,
    end_date: str,
    fetched: pd.DataFrame,
    *,
    source: str,
    vendor: str,
    cache_before: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Write vendor fetch to cache and return merged range + provenance."""
    write_stats = write_cached_bars(symbol, fetched, source=source, vendor=vendor)
    record_channel_stat("vendor_fetch", "ohlcv_daily")
    cached, _ = read_cached_bars(symbol, start_date, end_date)
    provenance = {
        **cache_before,
        "vendor_fetch": True,
        "vendor": vendor,
        "vendor_source": source,
        "source": source,
        "write": write_stats,
        "used_cache": bool(cache_before.get("cached_dates")),
        "final_rows": int(len(cached)),
    }
    return cached, provenance


def prefetch_symbols(
    symbols: list[str],
    *,
    days: int = 14,
    force: bool = False,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Warm hub OHLCV cache for many symbols (delegates to ``load_india_ohlcv``)."""
    from trade_integrations.dataflows.openalgo import load_india_ohlcv

    end = end_date or date.today().isoformat()
    start = start_date or (date.fromisoformat(end) - timedelta(days=max(days, 1))).isoformat()

    stats: dict[str, Any] = {
        "symbols": len(symbols),
        "loaded": 0,
        "cache_hits": 0,
        "vendor_fetches": 0,
        "failures": 0,
        "start_date": start,
        "end_date": end,
    }

    for symbol in symbols:
        try:
            _, prov = load_india_ohlcv(
                symbol,
                days=days,
                start_date=start,
                end_date=end,
                force_refresh=force,
                return_provenance=True,
            )
            if prov.get("final_rows", 0) > 0:
                stats["loaded"] += 1
            if prov.get("used_cache"):
                stats["cache_hits"] += 1
            if prov.get("vendor_fetch"):
                stats["vendor_fetches"] += 1
        except Exception as exc:
            stats["failures"] += 1
            logger.debug("prefetch failed for %s: %s", symbol, exc)

    stats["updated_at"] = _now_iso()
    return stats
