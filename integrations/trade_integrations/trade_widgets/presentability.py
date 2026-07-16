"""Gate trade-plan widget emission on payload completeness."""

from __future__ import annotations

from typing import Any


def _has_legs(widget: dict[str, Any]) -> bool:
    rec = widget.get("recommended") or {}
    if rec.get("legs"):
        return True
    for variant in (widget.get("strategy_variants") or {}).values():
        if isinstance(variant, dict) and variant.get("legs"):
            return True
    return False


def _options_presentable(widget: dict[str, Any], intent: str) -> bool:
    ranked = widget.get("ranked_strategies") or []
    variants = widget.get("strategy_variants") or {}
    charges = widget.get("charges") or {}
    net = charges.get("net_debit_credit")
    has_payoff = bool((widget.get("payoff") or {}).get("samples"))
    status = widget.get("plan_status", "")

    if intent in ("options_strategy", "execute_refresh") and not (ranked or variants):
        return False
    if status not in ("ready", "partial"):
        return False
    if not _has_legs(widget) and not ranked:
        return False
    if net is None:
        return False
    return has_payoff or _has_legs(widget)


def _index_presentable(widget: dict[str, Any]) -> bool:
    status = widget.get("plan_status", "")
    if status not in ("ready", "partial"):
        return False
    factor_exp = widget.get("factor_explanation") or {}
    return bool(
        factor_exp.get("contributors")
        or widget.get("top_factors")
        or widget.get("scenarios")
    )


def _stock_presentable(widget: dict[str, Any]) -> bool:
    if widget.get("plan_status") != "ready":
        return False
    steps = widget.get("implementation_steps") or []
    rec = widget.get("recommended") or {}
    return bool(steps or rec.get("action") or rec.get("side"))


def is_widget_presentable(widget: dict[str, Any], intent: str) -> bool:
    if not widget or intent == "none":
        return False

    asset = widget.get("asset_type", "options")

    if intent in ("options_strategy", "execute_refresh"):
        return _options_presentable(widget, intent)
    if intent == "index_outlook" or asset == "index":
        return _index_presentable(widget)
    if intent == "stock_trade" or asset == "stock":
        return _stock_presentable(widget)
    if asset == "options":
        return _options_presentable(widget, intent)
    return False


def presentation_mode_for(widget: dict[str, Any], intent: str) -> str:
    asset = widget.get("asset_type", "options")
    if asset == "index" or intent == "index_outlook":
        return "index_outlook"
    if asset == "stock" or intent == "stock_trade":
        return "stock_trade"
    return "options_strategy"


def default_widget_intent(widget: dict[str, Any]) -> str:
    asset = widget.get("asset_type", "options")
    if asset == "index":
        return "index_outlook"
    if asset == "stock":
        return "stock_trade"
    return "options_strategy"


def apply_widget_metadata(
    widget: dict[str, Any],
    widget_intent: str | None = None,
) -> dict[str, Any]:
    intent = widget_intent or default_widget_intent(widget)
    widget["widget_intent"] = intent
    widget["presentation_mode"] = presentation_mode_for(widget, intent)
    return widget
