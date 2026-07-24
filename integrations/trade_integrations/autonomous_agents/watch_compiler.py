"""Compile AgentIntent watch_conditions into Nautilus WatchSpec + schedules."""

from __future__ import annotations

import logging
from typing import Any

from trade_integrations.autonomous_agents.intent_schema import AgentIntent, WatchCondition

logger = logging.getLogger(__name__)


def _watch_exchange_for_symbol(symbol: str) -> str:
    sym = str(symbol or "").strip().upper()
    if sym in {"INDIAVIX", "INDIA VIX", "VIX"}:
        return "NSE"
    try:
        from trade_integrations.autonomous_agents.mandate_config import _watch_exchange_for_symbol as _legacy

        return _legacy(sym)
    except Exception:
        return "NSE"


def _compile_one_condition(cond: WatchCondition) -> list[dict[str, Any]]:
    sym = str(cond.symbol or "NIFTY").strip().upper()
    exchange = _watch_exchange_for_symbol(sym)
    params = dict(cond.params or {})
    label = cond.label
    rules: list[dict[str, Any]] = []

    if cond.kind == "schedule":
        return rules

    if cond.kind == "composite":
        children = params.get("conditions") or params.get("items") or []
        if isinstance(children, list):
            for row in children:
                if isinstance(row, dict):
                    child = WatchCondition.from_dict(row)
                    if child:
                        rules.extend(_compile_one_condition(child))
        return rules

    if cond.kind == "price_move":
        direction = str(params.get("direction") or "either").lower()
        if direction not in {"either", "up", "down"}:
            direction = "either"
        if params.get("pct") is not None:
            rules.append(
                {
                    "symbol": sym,
                    "metric": "spot_move_pct",
                    "threshold": float(params["pct"]),
                    "direction": direction,
                    "exchange": exchange,
                    "label": label or f"{sym} move {params['pct']}%",
                }
            )
        elif params.get("points") is not None:
            rules.append(
                {
                    "symbol": sym,
                    "metric": "spot_move_pct",
                    "threshold": float(params["points"]),
                    "direction": direction,
                    "exchange": exchange,
                    "label": label or f"{sym} move {params['points']} pts",
                    "_points_mode": True,
                }
            )
        return rules

    if cond.kind == "price_level":
        if params.get("above") is not None:
            rules.append(
                {
                    "symbol": sym,
                    "metric": "level_above",
                    "threshold": float(params["above"]),
                    "exchange": exchange,
                    "label": label or f"{sym} above {params['above']}",
                }
            )
        if params.get("below") is not None:
            rules.append(
                {
                    "symbol": sym,
                    "metric": "level_below",
                    "threshold": float(params["below"]),
                    "exchange": exchange,
                    "label": label or f"{sym} below {params['below']}",
                }
            )
        return rules

    if cond.kind == "volume":
        rules.append(
            {
                "symbol": sym,
                "metric": "volume_spike_pct",
                "threshold": float(params.get("pct") or params.get("threshold") or 50),
                "exchange": exchange,
                "label": label or f"{sym} volume spike",
            }
        )
        return rules

    if cond.kind == "oi":
        rules.append(
            {
                "symbol": sym,
                "metric": "oi_change_pct",
                "threshold": float(params.get("pct") or params.get("threshold") or 10),
                "exchange": exchange,
                "label": label or f"{sym} OI change",
            }
        )
        return rules

    if cond.kind == "vix":
        vix_sym = "INDIAVIX"
        if params.get("above") is not None:
            rules.append(
                {
                    "symbol": vix_sym,
                    "metric": "level_above",
                    "threshold": float(params["above"]),
                    "label": label or f"VIX above {params['above']}",
                }
            )
        if params.get("below") is not None:
            rules.append(
                {
                    "symbol": vix_sym,
                    "metric": "level_below",
                    "threshold": float(params["below"]),
                    "label": label or f"VIX below {params['below']}",
                }
            )
        return rules

    return rules


def _normalize_compiled_rules(rules: list[dict[str, Any]], *, spot: float | None = None) -> list[dict[str, Any]]:
    """Validate via WatchRule schema; convert points-mode to pct when spot known."""
    from nautilus_openalgo_bridge.models import WatchRule

    out: list[dict[str, Any]] = []
    for row in rules:
        patched = dict(row)
        if patched.pop("_points_mode", None):
            if not (spot and float(spot) > 0):
                logger.warning(
                    "skip points-based watch rule without spot price: %s",
                    patched.get("label") or patched.get("symbol"),
                )
                continue
            points = float(patched.get("threshold") or 0)
            patched["threshold"] = (points / float(spot)) * 100.0
            patched["metric"] = "spot_move_pct"
        try:
            validated = WatchRule.from_dict(patched)
            out.append(validated.to_dict())
        except (ValueError, TypeError) as exc:
            logger.warning("skip invalid compiled watch rule %s: %s", patched, exc)
            continue
    return out


def compile_watch_from_intent(
    intent: AgentIntent,
    *,
    symbols: list[str] | None = None,
    spot: float | None = None,
    cooldown_sec: int = 300,
    skip_if_unchanged_minutes: int | None = None,
) -> tuple[dict[str, int], dict[str, Any]]:
    """Return (schedules patch, watch_spec dict)."""
    sym_list = [str(s).strip().upper() for s in (symbols or intent.symbols or ["NIFTY"]) if str(s).strip()]
    schedules = dict(intent.schedules or {})
    rules: list[dict[str, Any]] = []

    for cond in intent.watch_conditions or []:
        if cond.kind == "schedule":
            every_min = cond.params.get("every_min")
            try:
                minutes = max(1, int(every_min))
                schedules["watch_ms"] = minutes * 60_000
            except (TypeError, ValueError):
                pass
            continue
        rules.extend(_compile_one_condition(cond))

    gate_minutes = skip_if_unchanged_minutes
    if gate_minutes is None and schedules.get("watch_ms"):
        gate_minutes = max(1, int(schedules["watch_ms"]) // 60_000)

    watch_spec: dict[str, Any] = {
        "rules": _normalize_compiled_rules(rules, spot=spot),
        "gate": {"skip_if_unchanged_minutes": int(gate_minutes or 5)},
        "cooldown_sec": int(cooldown_sec),
        "review_triggers": ["watch_rule_fired", "thesis_break", "news_material"],
    }
    if intent.engagement == "observe":
        watch_spec["review_triggers"] = ["watch_rule_fired", "news_material"]
    return schedules, watch_spec


def agent_has_user_watch_conditions(agent: dict[str, Any]) -> bool:
    """True when persisted intent includes user-authored watch conditions."""
    mc = agent.get("mandate_config") if isinstance(agent.get("mandate_config"), dict) else {}
    raw = mc.get("intent") if isinstance(mc.get("intent"), dict) else agent.get("intent")
    if not isinstance(raw, dict):
        return False
    from trade_integrations.autonomous_agents.intent_schema import AgentIntent

    intent = AgentIntent.from_dict(raw)
    return bool(intent.watch_conditions)
