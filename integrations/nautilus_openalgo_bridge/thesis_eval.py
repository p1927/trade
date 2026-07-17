"""Thesis break evaluation for Nautilus watch bridge."""

from __future__ import annotations

from typing import Any

from nautilus_openalgo_bridge.models import BridgeSignal, WatchAlert


def evaluate_thesis_for_agent(
    agent_id: str,
    *,
    live_spot: float | None = None,
    position_pnl: float | None = None,
) -> WatchAlert | None:
    """Return THESIS_BROKEN alert when mandate enables thesis_break and report fires."""
    try:
        from trade_integrations.autonomous_agents.store import get_agent
        from trade_integrations.auto_paper.mandate_config import mandate_config_from_agent
        from trade_integrations.context.hub import load_options_research_json, load_index_research_json
        from trade_integrations.monitor.thesis_break import evaluate_thesis_break
    except ImportError:
        return None

    agent = get_agent(agent_id)
    if not agent:
        return None

    mc = mandate_config_from_agent(agent)
    if not mc.alert_rules.thesis_break:
        return None

    symbols = list(agent.get("symbols") or ["NIFTY"])
    underlying = str(symbols[0]).upper()
    thesis = dict(agent.get("thesis") or {})
    widget_id = str(thesis.get("active_widget_id") or "")

    ledger_entry: dict[str, Any] = {
        "widget_id": widget_id,
        "underlying": underlying,
        "prediction_view": thesis.get("direction"),
        "plan_spot": thesis.get("entry_spot"),
    }

    doc = load_options_research_json(underlying) or load_index_research_json(underlying)
    if doc is None:
        return None

    report = evaluate_thesis_break(doc, ledger_entry, live_spot=live_spot, position_pnl=position_pnl)
    if not report.broken:
        return None

    flatten_on_break = mc.flatten_policy == "on_thesis_break"
    signal = BridgeSignal.EXIT_NOW if flatten_on_break else BridgeSignal.THESIS_BROKEN
    message = "; ".join(report.reasons[:3]) or "thesis break detected"
    return WatchAlert(
        signal=signal,
        rule=None,
        symbol=underlying,
        message=f"Thesis break: {message}",
        ltp=live_spot,
    )
