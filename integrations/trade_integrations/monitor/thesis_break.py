"""Detect when an open executed plan no longer matches live market reality."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

ThesisSeverity = Literal["low", "medium", "high"]


@dataclass
class ThesisBreakReport:
    broken: bool
    reasons: list[str]
    severity: ThesisSeverity
    widget_id: str | None = None
    underlying: str | None = None
    live_spot: float | None = None
    plan_spot: float | None = None
    position_pnl: float | None = None


def _max_loss_fraction() -> float:
    raw = os.getenv("THESIS_BREAK_MAX_LOSS_FRACTION", "0.8").strip()
    try:
        value = float(raw)
    except ValueError:
        return 0.8
    return min(max(value, 0.0), 1.0)


def _get_attr(doc: Any, name: str, default: Any = None) -> Any:
    if isinstance(doc, dict):
        return doc.get(name, default)
    return getattr(doc, name, default)


def _prediction_view(doc: Any, ledger_entry: dict[str, Any]) -> str:
    view = ledger_entry.get("prediction_view")
    if view:
        return str(view).strip().lower()
    prediction = _get_attr(doc, "prediction", {}) or {}
    if isinstance(prediction, dict):
        return str(prediction.get("view") or "neutral").strip().lower()
    return "neutral"


def _expected_move_pct(doc: Any) -> float | None:
    prediction = _get_attr(doc, "prediction", {}) or {}
    if not isinstance(prediction, dict):
        return None
    for key in ("expected_move_pct", "expected_move"):
        value = prediction.get(key)
        if value is None:
            continue
        try:
            return abs(float(value))
        except (TypeError, ValueError):
            continue
    return None


def _plan_spot(doc: Any, ledger_entry: dict[str, Any]) -> float | None:
    for source in (ledger_entry, doc if isinstance(doc, dict) else {}):
        value = source.get("plan_spot") if isinstance(source, dict) else None
        if value is None and not isinstance(source, dict):
            value = getattr(doc, "spot", None)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    spot = _get_attr(doc, "spot")
    if spot is None:
        return None
    try:
        return float(spot)
    except (TypeError, ValueError):
        return None


def _net_max_loss(doc: Any, ledger_entry: dict[str, Any]) -> float | None:
    for source in (
        ledger_entry,
        _get_attr(doc, "recommended", {}) or {},
        _get_attr(doc, "payoff", {}) or {},
    ):
        if not isinstance(source, dict):
            continue
        for key in ("net_max_loss", "max_loss"):
            value = source.get(key)
            if value is None:
                continue
            try:
                loss = float(value)
                if loss > 0:
                    return loss
                if loss < 0:
                    return abs(loss)
            except (TypeError, ValueError):
                continue
    return None


def _severity_rank(severity: ThesisSeverity) -> int:
    return {"low": 0, "medium": 1, "high": 2}[severity]


def _max_severity(current: ThesisSeverity, candidate: ThesisSeverity) -> ThesisSeverity:
    return candidate if _severity_rank(candidate) > _severity_rank(current) else current


def _is_adverse_scenario(scenario_name: str, prediction_view: str) -> bool:
    name = scenario_name.lower()
    if prediction_view in {"bullish", "bull"}:
        return "bear" in name or "breakdown" in name or "sell" in name
    if prediction_view in {"bearish", "bear"}:
        return "bull" in name or "breakout" in name or "rally" in name
    if prediction_view in {"range_bound", "neutral", "sideways"}:
        return name in {"bullish_breakout", "bearish_breakdown", "high_vol_event"}
    return False


def _scenario_trigger_hit(
    scenario: dict[str, Any],
    *,
    prediction_view: str,
    plan_spot: float,
    live_spot: float,
    expected_move_pct: float,
) -> bool:
    name = str(scenario.get("name") or "").lower()
    if not _is_adverse_scenario(name, prediction_view):
        return False

    move_pct = abs(live_spot - plan_spot) / plan_spot * 100.0
    threshold = max(expected_move_pct * 0.5, 0.25)

    if "bear" in name or "breakdown" in name or "sell" in name:
        return live_spot < plan_spot and move_pct >= threshold
    if "bull" in name or "breakout" in name or "rally" in name:
        return live_spot > plan_spot and move_pct >= threshold
    if "high_vol" in name or "vol" in name:
        return move_pct >= expected_move_pct
    return move_pct >= threshold


def evaluate_thesis_break(
    doc: Any,
    ledger_entry: dict[str, Any],
    *,
    live_spot: float | None,
    position_pnl: float | None,
) -> ThesisBreakReport:
    """Evaluate whether the executed plan thesis is broken."""
    widget_id = ledger_entry.get("widget_id")
    underlying = str(ledger_entry.get("underlying") or _get_attr(doc, "underlying", "") or "").upper()
    plan_spot = _plan_spot(doc, ledger_entry)
    reasons: list[str] = []
    severity: ThesisSeverity = "low"

    if plan_spot is None or plan_spot <= 0:
        return ThesisBreakReport(
            broken=False,
            reasons=["missing_plan_spot"],
            severity="low",
            widget_id=widget_id,
            underlying=underlying or None,
            live_spot=live_spot,
            plan_spot=plan_spot,
            position_pnl=position_pnl,
        )

    expected_move_pct = _expected_move_pct(doc)
    if live_spot is not None and expected_move_pct is not None:
        move_pct = abs(live_spot - plan_spot) / plan_spot * 100.0
        if move_pct > expected_move_pct:
            reasons.append("spot_outside_expected_move")
            severity = _max_severity(severity, "high")

    net_max_loss = _net_max_loss(doc, ledger_entry)
    if position_pnl is not None and net_max_loss is not None and net_max_loss > 0:
        if position_pnl < 0 and abs(position_pnl) >= _max_loss_fraction() * net_max_loss:
            reasons.append("max_loss_proximity")
            severity = _max_severity(severity, "medium")

    prediction_view = _prediction_view(doc, ledger_entry)
    scenarios = ledger_entry.get("scenarios") or _get_attr(doc, "scenarios", []) or []
    if live_spot is not None and expected_move_pct is not None:
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                continue
            if _scenario_trigger_hit(
                scenario,
                prediction_view=prediction_view,
                plan_spot=plan_spot,
                live_spot=live_spot,
                expected_move_pct=expected_move_pct,
            ):
                name = scenario.get("name") or "adverse_scenario"
                reasons.append(f"scenario_adverse:{name}")
                severity = _max_severity(severity, "high")
                break

    return ThesisBreakReport(
        broken=bool(reasons),
        reasons=reasons,
        severity=severity,
        widget_id=widget_id,
        underlying=underlying or None,
        live_spot=live_spot,
        plan_spot=plan_spot,
        position_pnl=position_pnl,
    )
