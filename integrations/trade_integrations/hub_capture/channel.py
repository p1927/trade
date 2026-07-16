"""Hub channel — read hub first, fetch vendor if stale, write-through capture copy."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.hub_capture.gate import should_capture
from trade_integrations.hub_capture.registry import capture_base_dir, get_entity, load_registry
from trade_integrations.hub_capture.writers import (
    record_chain_snapshot,
    record_news_snapshot,
    record_quote_snapshot,
)
from trade_integrations.hub_storage.parquet_io import read_dataframe

logger = logging.getLogger(__name__)

_STATS_REL = Path("_data") / "capture" / "channel_stats.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _options_cache_ttl_minutes() -> int:
    try:
        return max(0, int(os.getenv("TRADINGAGENTS_OPTIONS_CACHE_MINUTES", "30")))
    except ValueError:
        return 30


def _stats_path() -> Path:
    return get_hub_dir() / _STATS_REL


def _load_stats() -> dict[str, Any]:
    path = _stats_path()
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except (json.JSONDecodeError, OSError):
            pass
    return {"date": _today(), "hub_hits": 0, "vendor_fetches": 0, "by_series": {}}


def _save_stats(stats: dict[str, Any]) -> None:
    path = _stats_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")


def record_channel_stat(event: str, series: str) -> None:
    """Increment hub_hits or vendor_fetches for today."""
    stats = _load_stats()
    if stats.get("date") != _today():
        stats = {"date": _today(), "hub_hits": 0, "vendor_fetches": 0, "by_series": {}}
    counter_key = "hub_hits" if event == "hub_hit" else "vendor_fetches"
    stats[counter_key] = int(stats.get(counter_key) or 0) + 1
    by_series = stats.setdefault("by_series", {})
    bucket = by_series.setdefault(series, {"hub_hits": 0, "vendor_fetches": 0})
    bucket[counter_key] = int(bucket.get(counter_key) or 0) + 1
    stats["updated_at"] = _now_iso()
    _save_stats(stats)


def channel_stats_today() -> dict[str, Any]:
    """Return today's hub channel hit/fetch counters."""
    stats = _load_stats()
    if stats.get("date") != _today():
        return {"date": _today(), "hub_hits": 0, "vendor_fetches": 0, "by_series": {}}
    return stats


def resolve_registered_entity(symbol: str) -> str | None:
    """Return entity id when symbol is in capture registry."""
    key = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
    reg = load_registry(create=False)
    for entity in reg.get("entities") or []:
        eid = str(entity.get("id") or "").upper()
        if eid == key:
            return eid
    return None


def is_channel_active(entity_id: str) -> bool:
    entity = get_entity(entity_id)
    return bool(entity and entity.get("capture_enabled"))


def _options_latest_path(entity_id: str) -> Path:
    return get_hub_dir() / entity_id.upper() / "options_research" / "latest.json"


def _rebuild_chain_from_legs(leg_rows: pd.DataFrame) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    if leg_rows.empty or "strike" not in leg_rows.columns:
        return chain
    for strike, group in leg_rows.groupby("strike"):
        entry: dict[str, Any] = {"strike": strike}
        for _, leg in group.iterrows():
            opt = str(leg.get("option_type") or "").upper()
            if opt in ("CE", "PE"):
                entry[opt.lower()] = {
                    "ltp": leg.get("ltp"),
                    "oi": leg.get("oi"),
                    "iv": leg.get("iv"),
                    "volume": leg.get("volume"),
                }
        if entry.get("ce") or entry.get("pe"):
            chain.append(entry)
    return chain


def _chain_from_capture_today(entity_id: str) -> dict[str, Any] | None:
    path = capture_base_dir(entity_id) / "derivatives_chain" / f"{_today()}.parquet"
    frame = read_dataframe(path)
    if frame.empty or "series" not in frame.columns:
        return None
    summaries = frame[frame["series"] == "pcr_summary"]
    if summaries.empty:
        return None
    row = summaries.iloc[-1]
    legs = frame[frame["series"] == "derivatives_chain"]
    chain = _rebuild_chain_from_legs(legs)
    if not chain:
        return None
    return {
        "underlying": entity_id.upper(),
        "underlying_ltp": row.get("spot"),
        "expiry_date": row.get("expiry"),
        "chain": chain,
        "pcr": row.get("nifty_pcr"),
        "source": row.get("source") or "hub_capture",
        "channel": "hub_capture",
    }


def _chain_from_hub_latest(entity_id: str) -> tuple[dict[str, Any] | None, bool]:
    path = _options_latest_path(entity_id)
    if not path.is_file():
        captured = _chain_from_capture_today(entity_id)
        return captured, captured is not None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        captured = _chain_from_capture_today(entity_id)
        return captured, captured is not None

    as_of = payload.get("as_of") or payload.get("channel_patched_at")
    ttl = _options_cache_ttl_minutes()
    if as_of and ttl > 0:
        try:
            ts = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
            if age > ttl:
                captured = _chain_from_capture_today(entity_id)
                return captured, captured is not None
        except ValueError:
            pass

    chain_snap = dict(payload.get("chain_snapshot") or {})
    if not chain_snap.get("chain"):
        captured = _chain_from_capture_today(entity_id)
        return captured, captured is not None

    result = dict(chain_snap)
    result.setdefault("underlying", entity_id.upper())
    result["channel"] = "hub_latest"
    spot = payload.get("spot") or chain_snap.get("underlying_ltp")
    if spot is not None:
        result.setdefault("underlying_ltp", spot)
    if as_of is not None:
        result["hub_as_of"] = as_of
    return result, True


def _patch_options_latest(entity_id: str, chain_data: dict[str, Any], quote: dict[str, Any] | None = None) -> None:
    path = _options_latest_path(entity_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            payload = {}
    else:
        payload = {"underlying": entity_id.upper(), "asset_type": "options"}

    payload["chain_snapshot"] = chain_data
    payload["as_of"] = _now_iso()
    payload["channel_patched_at"] = _now_iso()
    if quote and quote.get("ltp") is not None:
        payload["spot"] = quote.get("ltp")
    elif chain_data.get("underlying_ltp") is not None:
        payload["spot"] = chain_data.get("underlying_ltp")
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def _write_through_chain(entity_id: str, chain_data: dict[str, Any], *, vendor: str = "openalgo") -> None:
    source = str(chain_data.get("source") or vendor)
    record_chain_snapshot(
        entity_id,
        chain_data,
        source=source,
        vendor=vendor,
        captured_at=_now_iso(),
    )
    _patch_options_latest(entity_id, chain_data)


def get_chain(
    underlying: str,
    exchange: str,
    fetch_fn: Callable[..., dict[str, Any]],
    *,
    expiry_date: str | None = None,
    strike_count: int | None = None,
) -> dict[str, Any]:
    """Hub-first option chain: read cache, vendor fetch, write-through capture."""
    entity = resolve_registered_entity(underlying)
    if entity is None:
        return fetch_fn(underlying, exchange, expiry_date=expiry_date, strike_count=strike_count)

    cached, fresh = _chain_from_hub_latest(entity)
    if fresh and cached and cached.get("chain"):
        record_channel_stat("hub_hit", "derivatives_chain")
        return cached

    data = fetch_fn(underlying, exchange, expiry_date=expiry_date, strike_count=strike_count)
    record_channel_stat("vendor_fetch", "derivatives_chain")
    if should_capture(entity, "derivatives_chain"):
        _write_through_chain(entity, data, vendor=str(data.get("source") or "openalgo"))
    return data


def get_quote(symbol: str, fetch_fn: Callable[[str], dict[str, Any] | None]) -> dict[str, Any] | None:
    """Hub-first live quote with write-through for registered entities."""
    entity = resolve_registered_entity(symbol)
    if entity is None:
        return fetch_fn(symbol)

    path = _options_latest_path(entity)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            as_of = payload.get("as_of") or payload.get("channel_patched_at")
            if as_of and _options_cache_ttl_minutes() > 0:
                ts = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - ts).total_seconds() / 60.0
                if age <= _options_cache_ttl_minutes() and payload.get("spot") is not None:
                    record_channel_stat("hub_hit", "quotes")
                    return {
                        "ltp": payload.get("spot"),
                        "source": "hub_latest",
                        "channel": "hub_latest",
                    }
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    quote = fetch_fn(symbol)
    if quote is None:
        return None
    record_channel_stat("vendor_fetch", "quotes")
    if should_capture(entity, "derivatives_chain"):
        record_quote_snapshot(entity, quote, source=str(quote.get("source") or "openalgo"))
        _patch_options_latest(entity, {}, quote=quote)
    return quote


def read_captured_pcr(entity_id: str = "NIFTY", *, day: str | None = None) -> float | None:
    """Latest PCR summary from capture ledger."""
    target_day = (day or _today())[:10]
    path = capture_base_dir(entity_id) / "derivatives_chain" / f"{target_day}.parquet"
    frame = read_dataframe(path)
    if frame.empty or "series" not in frame.columns:
        return None
    summaries = frame[frame["series"] == "pcr_summary"]
    if summaries.empty:
        return None
    val = summaries.iloc[-1].get("nifty_pcr")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def warm_entity_channel(entity_id: str, *, kind: str = "options") -> dict[str, Any]:
    """Pre-warm hub channel before research/orchestrator runs."""
    entity = resolve_registered_entity(entity_id)
    if not entity or not is_channel_active(entity):
        return {"status": "skipped", "reason": "not_registered_or_disabled"}
    summary: dict[str, Any] = {"entity_id": entity, "kind": kind}
    if kind in ("options", "index", "stock"):
        from trade_integrations.dataflows.openalgo import _fetch_live_quote_raw, _fetch_option_chain_raw

        try:
            chain = get_chain(entity, "NFO", _fetch_option_chain_raw, strike_count=15)
            summary["chain"] = {"status": "ok", "legs": len(chain.get("chain") or [])}
        except Exception as exc:
            summary["chain"] = {"status": "error", "error": str(exc)}
        try:
            quote = get_quote(entity, _fetch_live_quote_raw)
            summary["quote"] = {"status": "ok" if quote else "empty"}
        except Exception as exc:
            summary["quote"] = {"status": "error", "error": str(exc)}
    summary["status"] = "ok"
    return summary


def record_news_headlines(entity_id: str, headlines: list[dict[str, Any]], *, source: str) -> dict[str, Any]:
    """Write-through news headlines for registered index entities."""
    if not should_capture(entity_id, "flows"):
        return {"status": "skipped"}
    return record_news_snapshot(entity_id, headlines, source=source)
