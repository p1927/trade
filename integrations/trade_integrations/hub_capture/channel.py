"""Hub channel — read hub first, fetch vendor if stale, write-through capture copy."""

from __future__ import annotations

import json
import logging
import math
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
from trade_integrations.openalgo.freshness import FreshnessPolicy, L1Cache, ttl_seconds

logger = logging.getLogger(__name__)

_INDEX_SPOT_ENTITIES = frozenset({"NIFTY", "BANKNIFTY"})
_KNOWN_STALE_HUB_SPOTS = frozenset({24000.0})


def _index_spot_entity(entity_id: str | None) -> bool:
    return (entity_id or "").strip().upper() in _INDEX_SPOT_ENTITIES


def _hub_spot_is_stale_placeholder(spot: Any) -> bool:
    try:
        value = float(spot)
    except (TypeError, ValueError):
        return True
    return value in _KNOWN_STALE_HUB_SPOTS or value <= 0

_l1_cache = L1Cache()


def seed_quote_l1(symbol: str, exchange: str, quote: dict[str, Any]) -> None:
    """Push a live quote into the WATCH L1 cache (e.g. from OpenAlgo WebSocket)."""
    symbol_key, exchange_key = symbol.strip().upper(), exchange.strip().upper()
    l1_key = _quote_l1_key(symbol_key, exchange_key)
    ttl = int(ttl_seconds(FreshnessPolicy.WATCH))
    if ttl > 0:
        _l1_cache.set(l1_key, quote, ttl_seconds=ttl)


def _history_frame_from_cache(cached: pd.DataFrame) -> pd.DataFrame:
    """Convert hub OHLCV cache rows to TradingAgents Date/OHLCV columns."""
    if cached.empty:
        return cached
    frame = cached.copy()
    rename = {
        "date": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    for src, dst in rename.items():
        if src in frame.columns and dst not in frame.columns:
            frame = frame.rename(columns={src: dst})
    if "Date" not in frame.columns and "date" in frame.columns:
        frame["Date"] = pd.to_datetime(frame["date"], errors="coerce")
    elif "Date" in frame.columns:
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    return frame

_STATS_REL = Path("_data") / "capture" / "channel_stats.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


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
    return {"date": _today(), "hub_hits": 0, "l1_hits": 0, "vendor_fetches": 0, "by_series": {}}


def _save_stats(stats: dict[str, Any]) -> None:
    path = _stats_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")


def record_channel_stat(event: str, series: str) -> None:
    """Increment hub_hits, l1_hits, or vendor_fetches for today."""
    stats = _load_stats()
    if stats.get("date") != _today():
        stats = {"date": _today(), "hub_hits": 0, "l1_hits": 0, "vendor_fetches": 0, "by_series": {}}
    counter_key = {
        "hub_hit": "hub_hits",
        "l1_hit": "l1_hits",
        "vendor_fetch": "vendor_fetches",
    }.get(event, "vendor_fetches")
    stats[counter_key] = int(stats.get(counter_key) or 0) + 1
    by_series = stats.setdefault("by_series", {})
    bucket = by_series.setdefault(
        series, {"hub_hits": 0, "l1_hits": 0, "vendor_fetches": 0}
    )
    bucket[counter_key] = int(bucket.get(counter_key) or 0) + 1
    stats["updated_at"] = _now_iso()
    _save_stats(stats)


def channel_stats_today() -> dict[str, Any]:
    """Return today's hub channel hit/fetch counters."""
    stats = _load_stats()
    if stats.get("date") != _today():
        return {"date": _today(), "hub_hits": 0, "l1_hits": 0, "vendor_fetches": 0, "by_series": {}}
    return stats


def resolve_registered_entity(symbol: str | Any) -> str | None:
    """Return entity id when symbol is in capture registry."""
    if symbol is None:
        return None
    if not isinstance(symbol, str):
        base = getattr(symbol, "base_symbol", None) or getattr(symbol, "display_symbol", None)
        if isinstance(base, str) and base.strip():
            symbol = base
        else:
            symbol = str(symbol)
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


def _capture_chain_fallback(
    entity_id: str,
    *,
    policy: FreshnessPolicy,
) -> tuple[dict[str, Any] | None, bool]:
    """Return capture parquet chain for NORMAL; WATCH skips stale capture fallback."""
    if policy == FreshnessPolicy.WATCH:
        return None, False
    captured = _chain_from_capture_today(entity_id)
    return captured, captured is not None


def _chain_from_hub_latest(
    entity_id: str,
    *,
    max_age_seconds: float,
    policy: FreshnessPolicy = FreshnessPolicy.NORMAL,
) -> tuple[dict[str, Any] | None, bool]:
    path = _options_latest_path(entity_id)
    if not path.is_file():
        return _capture_chain_fallback(entity_id, policy=policy)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _capture_chain_fallback(entity_id, policy=policy)

    as_of = payload.get("as_of") or payload.get("channel_patched_at")
    if as_of and max_age_seconds > 0:
        try:
            ts = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age > max_age_seconds:
                return _capture_chain_fallback(entity_id, policy=policy)
        except ValueError:
            pass

    chain_snap = dict(payload.get("chain_snapshot") or {})
    if not chain_snap.get("chain"):
        return _capture_chain_fallback(entity_id, policy=policy)

    from trade_integrations.openalgo.market_data import chain_is_usable

    if not chain_is_usable(chain_snap):
        return _capture_chain_fallback(entity_id, policy=policy)

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
    from trade_integrations.openalgo.market_data import chain_is_usable

    record_chain_snapshot(
        entity_id,
        chain_data,
        source=source,
        vendor=vendor,
        captured_at=_now_iso(),
    )
    if chain_is_usable(chain_data):
        _patch_options_latest(entity_id, chain_data)


def get_chain(
    underlying: str,
    exchange: str,
    fetch_fn: Callable[..., dict[str, Any]],
    *,
    expiry_date: str | None = None,
    strike_count: int | None = None,
    policy: FreshnessPolicy = FreshnessPolicy.NORMAL,
) -> dict[str, Any]:
    """Hub-first option chain: read cache, vendor fetch, write-through capture."""
    entity = resolve_registered_entity(underlying)
    if entity is None:
        return fetch_fn(underlying, exchange, expiry_date=expiry_date, strike_count=strike_count)

    if policy == FreshnessPolicy.LIVE:
        data = fetch_fn(underlying, exchange, expiry_date=expiry_date, strike_count=strike_count)
        record_channel_stat("vendor_fetch", "derivatives_chain")
        if should_capture(entity, "derivatives_chain"):
            _write_through_chain(entity, data, vendor=str(data.get("source") or "openalgo"))
        return data

    max_age = float(ttl_seconds(policy))
    l1_key = f"{entity}:chain"

    if policy == FreshnessPolicy.WATCH:
        cached_l1 = _l1_cache.get(l1_key)
        if cached_l1 and cached_l1.get("chain"):
            record_channel_stat("l1_hit", "derivatives_chain")
            return cached_l1

    cached, fresh = _chain_from_hub_latest(entity, max_age_seconds=max_age, policy=policy)
    from trade_integrations.openalgo.market_data import chain_is_usable

    if fresh and cached and chain_is_usable(cached):
        record_channel_stat("hub_hit", "derivatives_chain")
        if policy == FreshnessPolicy.WATCH:
            _l1_cache.set(l1_key, cached, ttl_seconds=int(max_age))
        return cached

    data = fetch_fn(underlying, exchange, expiry_date=expiry_date, strike_count=strike_count)
    record_channel_stat("vendor_fetch", "derivatives_chain")
    if should_capture(entity, "derivatives_chain"):
        _write_through_chain(entity, data, vendor=str(data.get("source") or "openalgo"))
    if policy == FreshnessPolicy.WATCH:
        _l1_cache.set(l1_key, data, ttl_seconds=int(max_age))
    return data


def _quote_from_hub_latest(entity_id: str, *, max_age_seconds: float) -> dict[str, Any] | None:
    path = _options_latest_path(entity_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        as_of = payload.get("as_of") or payload.get("channel_patched_at")
        if as_of and max_age_seconds > 0:
            ts = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age > max_age_seconds:
                return None
        if payload.get("spot") is None:
            return None
        spot = payload.get("spot")
        if _index_spot_entity(entity_id) and _hub_spot_is_stale_placeholder(spot):
            return None
        return {
            "ltp": spot,
            "source": "hub_latest",
            "channel": "hub_latest",
        }
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def _normalize_quote_request(row: dict[str, str]) -> tuple[str, str] | None:
    if not isinstance(row, dict):
        return None
    symbol = row.get("symbol")
    exchange = row.get("exchange")
    if not symbol or not exchange:
        return None
    return str(symbol).upper(), str(exchange).upper()


def _quote_l1_key(symbol: str, exchange: str) -> str:
    return f"{symbol}:{exchange}:quote"


def get_multi_quotes(
    requests: list[dict[str, str]],
    fetch_fn: Callable[[list[dict[str, str]]], dict[str, Any]],
    *,
    policy: FreshnessPolicy = FreshnessPolicy.WATCH,
) -> dict[str, dict[str, Any]]:
    """Batch live quotes with per-(symbol, exchange) L1 dedupe."""
    from trade_integrations.openalgo.market_data import parse_multi_quotes_payload

    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in requests:
        pair = _normalize_quote_request(row)
        if pair is None or pair in seen:
            continue
        seen.add(pair)
        symbol, exchange = pair
        normalized.append({"symbol": symbol, "exchange": exchange})

    if not normalized:
        return {}

    max_age = int(ttl_seconds(policy))
    result: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, str]] = []

    if policy == FreshnessPolicy.LIVE:
        pending = normalized
    else:
        for req in normalized:
            symbol, exchange = req["symbol"], req["exchange"]
            l1_key = _quote_l1_key(symbol, exchange)
            cached_l1 = _l1_cache.get(l1_key)
            if cached_l1 is not None:
                record_channel_stat("l1_hit", "quotes")
                result[f"{symbol}@{exchange}"] = cached_l1
                continue

            if policy == FreshnessPolicy.NORMAL:
                entity = resolve_registered_entity(symbol)
                if entity is not None and not _index_spot_entity(entity):
                    hub_quote = _quote_from_hub_latest(entity, max_age_seconds=float(max_age))
                    if hub_quote is not None:
                        record_channel_stat("hub_hit", "quotes")
                        _l1_cache.set(l1_key, hub_quote, ttl_seconds=max_age)
                        result[f"{symbol}@{exchange}"] = hub_quote
                        continue

            pending.append(req)

    if pending:
        payload = fetch_fn(pending)
        record_channel_stat("vendor_fetch", "quotes")
        parsed = parse_multi_quotes_payload(payload)
        for req in pending:
            symbol, exchange = req["symbol"], req["exchange"]
            row_key = f"{symbol}@{exchange}"
            row = parsed.get(row_key)
            if row is None:
                continue
            l1_key = _quote_l1_key(symbol, exchange)
            if policy != FreshnessPolicy.LIVE and max_age > 0:
                _l1_cache.set(l1_key, row, ttl_seconds=max_age)
            entity = resolve_registered_entity(symbol)
            if entity is not None and should_capture(entity, "quotes"):
                record_quote_snapshot(entity, row, source=str(row.get("source") or "openalgo"))
            result[row_key] = row

    return result


def get_history(
    symbol: str,
    start: str,
    end: str,
    interval: str,
    fetch_fn: Callable[..., pd.DataFrame],
    *,
    policy: FreshnessPolicy = FreshnessPolicy.NORMAL,
) -> pd.DataFrame:
    """OHLCV history with hub parquet cache, L1 dedupe, then vendor fetch."""
    sym = symbol.strip().upper()
    start_key = start[:10]
    end_key = end[:10]
    cache_key = f"{sym}:{start_key}:{end_key}:{interval}:history"
    max_age = int(ttl_seconds(policy))

    entity = resolve_registered_entity(symbol)
    if entity is not None and policy != FreshnessPolicy.LIVE:
        try:
            from trade_integrations.hub_capture.ohlcv_cache import read_cached_bars

            cached, _meta = read_cached_bars(symbol, start_key, end_key)
            if not cached.empty and len(cached) >= 5:
                frame = _history_frame_from_cache(cached)
                if policy != FreshnessPolicy.LIVE and max_age > 0:
                    _l1_cache.set(cache_key, frame.copy(), ttl_seconds=max_age)
                return frame
        except Exception as exc:
            logger.debug("hub ohlcv cache read failed for %s: %s", symbol, exc)

    if policy != FreshnessPolicy.LIVE and max_age > 0:
        cached = _l1_cache.get(cache_key)
        if cached is not None:
            record_channel_stat("l1_hit", "ohlcv_daily")
            return cached.copy()

    frame = fetch_fn(symbol, start, end, interval=interval)
    record_channel_stat("vendor_fetch", "ohlcv_daily")

    if entity is not None and should_capture(entity, "ohlcv_daily") and not frame.empty:
        try:
            from trade_integrations.dataflows.openalgo import to_index_research_frame
            from trade_integrations.hub_capture.ohlcv_cache import merge_with_cache

            index_frame = to_index_research_frame(frame)
            merge_with_cache(
                symbol,
                start_key,
                end_key,
                index_frame,
                source="openalgo",
                vendor="openalgo",
                cache_before={},
            )
        except Exception as exc:
            logger.debug("hub ohlcv cache write failed for %s: %s", symbol, exc)

    if policy != FreshnessPolicy.LIVE and max_age > 0:
        _l1_cache.set(cache_key, frame.copy(), ttl_seconds=max_age)
    return frame


def get_quote(
    symbol: str,
    fetch_fn: Callable[[str], dict[str, Any] | None],
    *,
    policy: FreshnessPolicy = FreshnessPolicy.NORMAL,
) -> dict[str, Any] | None:
    """Hub-first live quote with write-through for registered entities."""
    entity = resolve_registered_entity(symbol)
    if entity is None:
        return fetch_fn(symbol)

    if policy == FreshnessPolicy.LIVE:
        quote = fetch_fn(symbol)
        if quote is None or quote.get("ltp") in (None, 0):
            return None
        record_channel_stat("vendor_fetch", "quotes")
        if should_capture(entity, "derivatives_chain"):
            record_quote_snapshot(entity, quote, source=str(quote.get("source") or "openalgo"))
            _patch_options_latest(entity, {}, quote=quote)
        return quote

    max_age = float(ttl_seconds(policy))
    l1_key = f"{entity}:quotes"

    if policy == FreshnessPolicy.WATCH:
        cached_l1 = _l1_cache.get(l1_key)
        if cached_l1 is not None and not _index_spot_entity(entity):
            record_channel_stat("l1_hit", "quotes")
            return cached_l1

    hub_quote = None
    if not _index_spot_entity(entity):
        hub_quote = _quote_from_hub_latest(entity, max_age_seconds=max_age)
    if hub_quote is not None:
        record_channel_stat("hub_hit", "quotes")
        if policy == FreshnessPolicy.WATCH:
            _l1_cache.set(l1_key, hub_quote, ttl_seconds=int(max_age))
        return hub_quote

    quote = fetch_fn(symbol)
    if quote is None or quote.get("ltp") in (None, 0):
        return None
    record_channel_stat("vendor_fetch", "quotes")
    if should_capture(entity, "derivatives_chain"):
        record_quote_snapshot(entity, quote, source=str(quote.get("source") or "openalgo"))
        _patch_options_latest(entity, {}, quote=quote)
    if policy == FreshnessPolicy.WATCH:
        _l1_cache.set(l1_key, quote, ttl_seconds=int(max_age))
    return quote


def read_captured_pcr(entity_id: str = "NIFTY", *, day: str | None = None) -> float | None:
    """Latest valid PCR summary from capture ledger (skips NaN / partial captures)."""
    target_day = (day or _today())[:10]
    path = capture_base_dir(entity_id) / "derivatives_chain" / f"{target_day}.parquet"
    frame = read_dataframe(path)
    if frame.empty or "series" not in frame.columns:
        return None
    summaries = frame[frame["series"] == "pcr_summary"]
    if summaries.empty:
        return None
    for raw in reversed(summaries["nifty_pcr"].tolist()):
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isnan(value):
            continue
        return value
    return None


def warm_entity_channel(entity_id: str, *, kind: str = "options") -> dict[str, Any]:
    """Pre-warm hub channel before research/orchestrator runs."""
    entity = resolve_registered_entity(entity_id)
    if not entity or not is_channel_active(entity):
        return {"status": "skipped", "reason": "not_registered_or_disabled"}
    summary: dict[str, Any] = {"entity_id": entity, "kind": kind}
    if kind in ("options", "index", "stock"):
        from trade_integrations.openalgo.market_data import (
            fetch_option_chain_channel_vendor,
        )
        from trade_integrations.dataflows.openalgo import _fetch_live_quote_raw

        try:
            chain = get_chain(entity, "NFO", fetch_option_chain_channel_vendor, strike_count=15)
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
