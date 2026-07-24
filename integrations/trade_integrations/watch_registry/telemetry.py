"""Live watch telemetry for UI — quotes + per-rule distance to threshold."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from trade_integrations.watch_registry.api import mcp_list_watches

# Baseline LTP cache keyed by "{watch_id}:{symbol}" — mirrors poll_loop seeding.
_baseline_ltp_cache: dict[str, float] = {}
_baseline_oi_cache: dict[str, float] = {}
_baseline_volume_cache: dict[str, float] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_key(watch_id: str, symbol: str) -> str:
    return f"{watch_id}:{symbol.upper()}"


def _move_pct(baseline: float, current: float) -> float:
    if baseline == 0:
        return 0.0
    return ((current - baseline) / baseline) * 100.0


def _format_rule_condition_text(rule: dict[str, Any]) -> str:
    from trade_integrations.autonomous_agents.strategy_watch_spec import format_watch_spec_summary

    return format_watch_spec_summary({"rules": [rule]})


def _resolve_owner_watches(
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """agent_id takes precedence over session_id when both are provided."""
    if agent_id:
        listed = mcp_list_watches(agent_id=agent_id)
        watches = list(listed.get("watches") or [])
        if not watches:
            try:
                from trade_integrations.watch_registry.store import migrate_agent_watch_spec_to_registry

                migrate_agent_watch_spec_to_registry(agent_id)
                listed = mcp_list_watches(agent_id=agent_id)
                watches = list(listed.get("watches") or [])
            except Exception:
                pass
        return watches
    if session_id:
        listed = mcp_list_watches(session_id=session_id)
        return list(listed.get("watches") or [])
    return []


def _collect_symbols(watches: list[dict[str, Any]]) -> list[str]:
    symbols: set[str] = set()
    for watch in watches:
        for sym in watch.get("symbols") or []:
            if str(sym).strip():
                symbols.add(str(sym).upper())
        spec = watch.get("watch_spec") or {}
        for row in spec.get("rules") or []:
            if isinstance(row, dict) and row.get("symbol"):
                symbols.add(str(row["symbol"]).upper())
    return sorted(symbols)


def _poll_quotes(symbols: list[str]) -> dict[str, Any]:
    if not symbols:
        return {}
    try:
        from nautilus_openalgo_bridge.data_feed import OpenAlgoQuoteFeed

        return OpenAlgoQuoteFeed().poll(symbols=symbols)
    except Exception:
        return {}


def _seed_baseline(watch_id: str, symbol: str, quote: Any, *, field: str) -> float | None:
    key = _cache_key(watch_id, symbol)
    if field == "ltp":
        cache = _baseline_ltp_cache
        value = quote.ltp
    elif field == "oi":
        cache = _baseline_oi_cache
        value = quote.oi
    elif field == "volume":
        cache = _baseline_volume_cache
        value = quote.volume
    else:
        return None
    if value is None:
        return cache.get(key)
    if key not in cache:
        cache[key] = float(value)
    return cache.get(key)


def _resolve_baseline_ltp(watch_id: str, rule: dict[str, Any], quote: Any) -> float | None:
    raw = rule.get("baseline_ltp")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return _seed_baseline(watch_id, rule.get("symbol") or quote.symbol, quote, field="ltp")


def _rule_telemetry(
    watch_id: str,
    rule: dict[str, Any],
    quotes: dict[str, Any],
) -> dict[str, Any]:
    metric = str(rule.get("metric") or rule.get("type") or "spot_move_pct")
    symbol = str(rule.get("symbol") or "").upper()
    threshold = float(rule.get("threshold") or rule.get("value") or 0)
    direction = str(rule.get("direction") or "either").lower()
    condition_text = _format_rule_condition_text(rule)

    if metric == "session_close":
        return {
            "symbol": symbol or "?",
            "metric": metric,
            "condition_text": condition_text,
            "threshold": threshold,
            "direction": direction,
            "current": {},
            "distance": {"fired": False, "remaining": None, "unit": "points"},
            "quote_available": False,
        }

    quote = quotes.get(symbol) or quotes.get(symbol.upper())
    if quote is None:
        return {
            "symbol": symbol or "?",
            "metric": metric,
            "condition_text": condition_text,
            "threshold": threshold,
            "direction": direction,
            "current": {},
            "distance": {"fired": False, "remaining": None, "unit": "pct"},
            "quote_available": False,
        }

    ltp = float(quote.ltp)
    current: dict[str, Any] = {"ltp": ltp}

    if metric == "spot_move_pct":
        baseline = _resolve_baseline_ltp(watch_id, rule, quote)
        if baseline is None or baseline <= 0:
            return {
                "symbol": symbol,
                "metric": metric,
                "condition_text": condition_text,
                "threshold": threshold,
                "direction": direction,
                "current": {"ltp": ltp},
                "distance": {"fired": False, "remaining": None, "unit": "pct"},
                "quote_available": True,
            }
        move = _move_pct(baseline, ltp)
        current["baseline_ltp"] = baseline
        current["move_pct"] = round(move, 4)
        abs_move = abs(move)
        fired = abs_move >= threshold
        if fired and direction == "up" and move < 0:
            fired = False
        if fired and direction == "down" and move > 0:
            fired = False
        remaining = max(0.0, threshold - abs_move) if not fired else 0.0
        return {
            "symbol": symbol,
            "metric": metric,
            "condition_text": condition_text,
            "threshold": threshold,
            "direction": direction,
            "current": current,
            "distance": {"fired": fired, "remaining": round(remaining, 4), "unit": "pct"},
            "quote_available": True,
        }

    if metric == "level_above":
        fired = ltp > threshold
        remaining = max(0.0, threshold - ltp) if not fired else 0.0
        return {
            "symbol": symbol,
            "metric": metric,
            "condition_text": condition_text,
            "threshold": threshold,
            "direction": direction,
            "current": current,
            "distance": {"fired": fired, "remaining": round(remaining, 4), "unit": "points"},
            "quote_available": True,
        }

    if metric == "level_below":
        fired = ltp < threshold
        remaining = max(0.0, ltp - threshold) if not fired else 0.0
        return {
            "symbol": symbol,
            "metric": metric,
            "condition_text": condition_text,
            "threshold": threshold,
            "direction": direction,
            "current": current,
            "distance": {"fired": fired, "remaining": round(remaining, 4), "unit": "points"},
            "quote_available": True,
        }

    if metric == "oi_change_pct":
        oi_base = _seed_baseline(watch_id, symbol, quote, field="oi")
        if oi_base is None or oi_base <= 0 or quote.oi is None:
            current["oi"] = quote.oi
            return {
                "symbol": symbol,
                "metric": metric,
                "condition_text": condition_text,
                "threshold": threshold,
                "direction": direction,
                "current": current,
                "distance": {"fired": False, "remaining": None, "unit": "pct"},
                "quote_available": quote.oi is not None,
            }
        change = _move_pct(oi_base, float(quote.oi))
        current["oi"] = float(quote.oi)
        current["move_pct"] = round(change, 4)
        abs_change = abs(change)
        fired = abs_change >= threshold
        if fired and direction == "up" and change < 0:
            fired = False
        if fired and direction == "down" and change > 0:
            fired = False
        remaining = max(0.0, threshold - abs_change) if not fired else 0.0
        return {
            "symbol": symbol,
            "metric": metric,
            "condition_text": condition_text,
            "threshold": threshold,
            "direction": direction,
            "current": current,
            "distance": {"fired": fired, "remaining": round(remaining, 4), "unit": "pct"},
            "quote_available": True,
        }

    if metric == "volume_spike_pct":
        vol_base = _seed_baseline(watch_id, symbol, quote, field="volume")
        if vol_base is None or vol_base <= 0 or quote.volume is None:
            current["volume"] = quote.volume
            return {
                "symbol": symbol,
                "metric": metric,
                "condition_text": condition_text,
                "threshold": threshold,
                "direction": direction,
                "current": current,
                "distance": {"fired": False, "remaining": None, "unit": "pct"},
                "quote_available": quote.volume is not None,
            }
        spike = _move_pct(vol_base, float(quote.volume))
        current["volume"] = float(quote.volume)
        current["move_pct"] = round(spike, 4)
        fired = spike >= threshold
        remaining = max(0.0, threshold - spike) if not fired else 0.0
        return {
            "symbol": symbol,
            "metric": metric,
            "condition_text": condition_text,
            "threshold": threshold,
            "direction": direction,
            "current": current,
            "distance": {"fired": fired, "remaining": round(remaining, 4), "unit": "pct"},
            "quote_available": True,
        }

    return {
        "symbol": symbol or "?",
        "metric": metric,
        "condition_text": condition_text,
        "threshold": threshold,
        "direction": direction,
        "current": current,
        "distance": {"fired": False, "remaining": None, "unit": "pct"},
        "quote_available": True,
    }


def _market_open() -> bool | None:
    try:
        from nautilus_openalgo_bridge.config import is_bridge_market_open

        return bool(is_bridge_market_open())
    except Exception:
        return None


def build_watches_live_snapshot(
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    watches = _resolve_owner_watches(session_id=session_id, agent_id=agent_id)
    symbols = _collect_symbols(watches)
    quotes = _poll_quotes(symbols)

    snapshots: list[dict[str, Any]] = []
    for watch in watches:
        watch_id = str(watch.get("watch_id") or "")
        spec = watch.get("watch_spec") or {}
        rules_out: list[dict[str, Any]] = []
        for row in spec.get("rules") or []:
            if not isinstance(row, dict):
                continue
            try:
                rules_out.append(_rule_telemetry(watch_id, row, quotes))
            except Exception:
                rules_out.append(
                    {
                        "symbol": str(row.get("symbol") or "?"),
                        "metric": str(row.get("metric") or ""),
                        "condition_text": _format_rule_condition_text(row),
                        "threshold": float(row.get("threshold") or 0),
                        "direction": str(row.get("direction") or "either"),
                        "current": {},
                        "distance": {"fired": False, "remaining": None, "unit": "pct"},
                        "quote_available": False,
                    }
                )
        snapshots.append(
            {
                "watch_id": watch_id,
                "label": watch.get("label"),
                "rules": rules_out,
                "last_fired_at": watch.get("last_fired_at"),
            }
        )

    return {
        "status": "ok",
        "fetched_at": _now_iso(),
        "market_open": _market_open(),
        "watches": snapshots,
        "count": len(snapshots),
    }


def clear_telemetry_baseline_cache() -> None:
    """Test helper — reset in-process baseline caches."""
    _baseline_ltp_cache.clear()
    _baseline_oi_cache.clear()
    _baseline_volume_cache.clear()
