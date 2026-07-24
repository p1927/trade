"""Live watch telemetry for UI — quotes + per-rule distance to threshold."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from trade_integrations.watch_registry.api import mcp_list_watches
from trade_integrations.watch_registry.baselines import prune_owner_baselines, seed_owner_baseline
from trade_integrations.watch_registry.scope import (
    OWNER_KIND_AUTONOMOUS,
    OWNER_KIND_SESSION,
    nautilus_owner_id,
    symbols_for_owner,
)

logger = logging.getLogger(__name__)

# Per-owner symbol baselines — same semantics as Nautilus poll_loop / WatchActor.
_baseline_ltp_cache: dict[str, float] = {}
_baseline_oi_cache: dict[str, float] = {}
_baseline_volume_cache: dict[str, float] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_nautilus_owner(
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
) -> str | None:
    """Map API owner params to Nautilus owner id (``aa_*`` or ``ws_{session}``)."""
    if agent_id:
        aid = str(agent_id).strip()
        if not aid:
            return None
        if aid.startswith("aa_"):
            return aid
        return nautilus_owner_id(owner_kind=OWNER_KIND_AUTONOMOUS, owner_id=aid)
    if session_id:
        sid = str(session_id).strip()
        if not sid:
            return None
        return nautilus_owner_id(owner_kind=OWNER_KIND_SESSION, owner_id=sid)
    return None


def _move_pct(baseline: float, current: float) -> float:
    if baseline == 0:
        return 0.0
    return ((current - baseline) / baseline) * 100.0


def _format_rule_condition_text(rule: dict[str, Any]) -> str:
    from trade_integrations.autonomous_agents.strategy_watch_spec import format_watch_spec_summary

    return format_watch_spec_summary({"rules": [rule]})


def _synthetic_watches_from_agent(agent_id: str) -> list[dict[str, Any]]:
    """Build ephemeral watch rows from agent watch_spec when registry is empty."""
    try:
        from trade_integrations.autonomous_agents.store import get_agent
    except Exception:
        return []
    agent = get_agent(agent_id) or {}
    raw = agent.get("watch_spec") or (agent.get("mandate_config") or {}).get("watch_spec")
    if not isinstance(raw, dict) or not raw.get("rules"):
        return []
    return [
        {
            "watch_id": f"agent:{agent_id}",
            "label": "strategy watch",
            "symbols": list(agent.get("symbols") or []),
            "watch_spec": raw,
            "last_fired_at": None,
        }
    ]


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
            watches = _synthetic_watches_from_agent(agent_id)
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
        logger.warning("watch telemetry quote poll failed", exc_info=True)
        return {}


def _seed_baseline(nautilus_owner: str, symbol: str, quote: Any, *, field: str) -> float | None:
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
    return seed_owner_baseline(cache, nautilus_owner=nautilus_owner, symbol=symbol, value=value)


def _resolve_baseline_ltp(nautilus_owner: str, rule: dict[str, Any], quote: Any) -> float | None:
    symbol = rule.get("symbol") or quote.symbol
    # Match Nautilus evaluate_rule: seeded cache wins over rule.baseline_ltp.
    cached = seed_owner_baseline(
        _baseline_ltp_cache,
        nautilus_owner=nautilus_owner,
        symbol=str(symbol or ""),
        value=None,
    )
    if cached is not None:
        return cached
    seeded = _seed_baseline(nautilus_owner, symbol, quote, field="ltp")
    if seeded is not None:
        return seeded
    raw = rule.get("baseline_ltp")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return None


def _rule_telemetry(
    nautilus_owner: str,
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
        baseline = _resolve_baseline_ltp(nautilus_owner, rule, quote)
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
        oi_base = _seed_baseline(nautilus_owner, symbol, quote, field="oi")
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
        vol_base = _seed_baseline(nautilus_owner, symbol, quote, field="volume")
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
    nautilus_owner = _resolve_nautilus_owner(session_id=session_id, agent_id=agent_id)
    watches = _resolve_owner_watches(session_id=session_id, agent_id=agent_id)
    if nautilus_owner:
        sync_telemetry_baselines_for_owner(
            nautilus_owner,
            active_symbols=set(_collect_symbols(watches)),
        )
    symbols = _collect_symbols(watches)
    quotes = _poll_quotes(symbols)
    quotes_ok = (not symbols) or bool(quotes)

    snapshots: list[dict[str, Any]] = []
    for watch in watches:
        watch_id = str(watch.get("watch_id") or "")
        spec = watch.get("watch_spec") or {}
        rules_out: list[dict[str, Any]] = []
        owner_key = nautilus_owner or ""
        for row in spec.get("rules") or []:
            if not isinstance(row, dict):
                continue
            try:
                rules_out.append(_rule_telemetry(owner_key, row, quotes))
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
        "status": "ok" if quotes_ok else "degraded",
        "fetched_at": _now_iso(),
        "market_open": _market_open(),
        "quotes_ok": quotes_ok,
        "watches": snapshots,
        "count": len(snapshots),
    }


def clear_telemetry_baseline_cache() -> None:
    """Test helper — reset in-process baseline caches."""
    _baseline_ltp_cache.clear()
    _baseline_oi_cache.clear()
    _baseline_volume_cache.clear()


def sync_telemetry_baselines_for_owner(
    nautilus_owner: str,
    *,
    active_symbols: set[str] | frozenset[str] | None = None,
) -> None:
    """Drop cached baselines for symbols no longer watched by this owner."""
    owner = str(nautilus_owner or "").strip()
    if not owner:
        return
    active = active_symbols if active_symbols is not None else set(symbols_for_owner(owner))
    prune_owner_baselines(
        (_baseline_ltp_cache, _baseline_oi_cache, _baseline_volume_cache),
        nautilus_owner=owner,
        active_symbols=active,
    )
