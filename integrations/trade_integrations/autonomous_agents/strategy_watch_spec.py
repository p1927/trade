"""Derive Nautilus watch rules from the chosen strategy — not a generic mandate dump."""

from __future__ import annotations

from typing import Any

from trade_integrations.auto_paper.mandate_config import MandateConfig, to_watch_spec


def _norm_strategy(name: str | None) -> str:
    return str(name or "").strip().lower().replace(" ", "_").replace("-", "_")


def build_watch_spec_for_strategy(
    *,
    strategy: str,
    mandate: MandateConfig,
    symbols: list[str],
    spot: float | None = None,
    target: float | None = None,
    stop: float | None = None,
) -> dict[str, Any]:
    """Build strategy-specific watch rules on top of mandate defaults."""
    base = to_watch_spec(mandate, symbols=symbols)
    focus = (symbols[0] if symbols else "NIFTY").upper()
    exchange = "NSE"
    try:
        from trade_integrations.auto_paper.mandate_config import _watch_exchange_for_symbol

        exchange = _watch_exchange_for_symbol(focus)
    except Exception:
        pass

    key = _norm_strategy(strategy)
    spot_pct = float(mandate.alert_rules.spot_move_pct or 0.5)
    rules: list[dict[str, Any]] = []
    review_triggers = ["watch_rule_fired"]

    if key in {"hold_cash", "hold", "skip", "wait"}:
        rules.append(
            {
                "symbol": focus,
                "metric": "spot_move_pct",
                "threshold": max(spot_pct, 1.0),
                "direction": "either",
                "exchange": exchange,
                "label": f"{focus} entry setup",
            }
        )
        if target and float(target) > 0:
            rules.append(
                {
                    "symbol": focus,
                    "metric": "level_below",
                    "threshold": float(target),
                    "exchange": exchange,
                    "label": f"{focus} dip target",
                }
            )
        review_triggers = ["watch_rule_fired", "news_material"]

    elif key in {"buy_dip", "dip", "accumulate"}:
        rules.append(
            {
                "symbol": focus,
                "metric": "spot_move_pct",
                "threshold": spot_pct,
                "direction": "down",
                "exchange": exchange,
                "label": f"{focus} pullback",
            }
        )
        if stop and float(stop) > 0:
            rules.append(
                {
                    "symbol": focus,
                    "metric": "level_below",
                    "threshold": float(stop),
                    "exchange": exchange,
                    "label": f"{focus} stop",
                }
            )
        if mandate.alert_rules.thesis_break:
            review_triggers.append("thesis_break")

    elif key in {"momentum_breakout", "breakout", "momentum", "trend_follow"}:
        rules.append(
            {
                "symbol": focus,
                "metric": "spot_move_pct",
                "threshold": spot_pct,
                "direction": "up",
                "exchange": exchange,
                "label": f"{focus} breakout",
            }
        )
        if stop and float(stop) > 0:
            rules.append(
                {
                    "symbol": focus,
                    "metric": "level_below",
                    "threshold": float(stop),
                    "exchange": exchange,
                    "label": f"{focus} stop",
                }
            )
        if mandate.alert_rules.thesis_break:
            review_triggers.append("thesis_break")

    elif key in {"event_play", "event", "earnings", "catalyst"}:
        rules.append(
            {
                "symbol": focus,
                "metric": "spot_move_pct",
                "threshold": max(spot_pct, 0.75),
                "direction": "either",
                "exchange": exchange,
                "label": f"{focus} event vol",
            }
        )
        if mandate.alert_rules.vix_above is not None and "options" in mandate.allowed_instruments:
            rules.append(
                {
                    "symbol": "INDIAVIX",
                    "metric": "level_above",
                    "threshold": mandate.alert_rules.vix_above,
                    "label": "VIX spike",
                }
            )
        review_triggers.extend(["news_material", "thesis_break"])

    elif key in {"iron_condor", "short_strangle", "credit_spread", "income"}:
        rules.append(
            {
                "symbol": focus,
                "metric": "spot_move_pct",
                "threshold": max(spot_pct, 0.4),
                "direction": "either",
                "exchange": exchange,
                "label": f"{focus} range breach",
            }
        )
        if mandate.alert_rules.vix_above is not None:
            rules.append(
                {
                    "symbol": "INDIAVIX",
                    "metric": "level_above",
                    "threshold": mandate.alert_rules.vix_above,
                    "label": "VIX expansion",
                }
            )
        review_triggers.extend(["thesis_break"])

    elif key in {"long_call", "long_put", "directional", "bull_call", "bear_put"}:
        rules.append(
            {
                "symbol": focus,
                "metric": "spot_move_pct",
                "threshold": spot_pct,
                "direction": "either",
                "exchange": exchange,
                "label": f"{focus} move",
            }
        )
        if stop and float(stop) > 0:
            rules.append(
                {
                    "symbol": focus,
                    "metric": "level_below",
                    "threshold": float(stop),
                    "exchange": exchange,
                    "label": f"{focus} stop",
                }
            )
        review_triggers.append("thesis_break")

    else:
        rules = list(base.get("rules") or [])

    if mandate.needs_session_close_flatten():
        rules.append(
            {
                "symbol": focus,
                "metric": "session_close",
                "threshold": 0,
                "exchange": exchange,
                "label": "flatten at close",
            }
        )

    if mandate.alert_rules.pnl_loss_inr and key not in {"hold_cash", "hold", "skip", "wait"}:
        review_triggers.append("thesis_break")

    cooldown = int(base.get("cooldown_sec") or 300)
    return {
        "rules": rules,
        "gate": dict(base.get("gate") or {"skip_if_unchanged_minutes": 5}),
        "cooldown_sec": cooldown,
        "review_triggers": list(dict.fromkeys(review_triggers)),
        "strategy": key or strategy,
        "derived_from": "strategy_watch_spec",
    }


def format_watch_spec_summary(watch_spec: dict[str, Any]) -> str:
    """Human-readable one-liner for chat / approval card."""
    rules = watch_spec.get("rules") or []
    parts: list[str] = []
    for row in rules[:6]:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or row.get("symbol") or "?")
        metric = str(row.get("metric") or "")
        if metric == "spot_move_pct":
            direction = row.get("direction") or "either"
            parts.append(f"{label} move {direction} ≥{row.get('threshold')}%")
        elif metric in {"level_above", "level_below"}:
            parts.append(f"{label} {metric.replace('_', ' ')} {row.get('threshold')}")
        elif metric == "session_close":
            parts.append("flatten at session close")
        else:
            parts.append(label)
    strategy = watch_spec.get("strategy")
    prefix = f"strategy={strategy} · " if strategy else ""
    return prefix + (" · ".join(parts) if parts else "no rules")
