"""Shared actions invoked by OpenAlgo MCP auto-paper tools."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.auto_paper.agent_mandate import DEFAULT_GOAL, session_summary_for_status
from trade_integrations.auto_paper.audit import write_paper_action
from trade_integrations.auto_paper.config import get_auto_paper_config
from trade_integrations.auto_paper.engine import is_market_session_open
from trade_integrations.auto_paper.market_feedback import build_market_feedback
from trade_integrations.auto_paper.openalgo_client import OpenAlgoClient
from trade_integrations.auto_paper.reconcile import reconcile_paper_state
from trade_integrations.auto_paper.scheduler_cleanup import remove_auto_paper_scheduler_jobs
from trade_integrations.auto_paper.lifecycle import default_lifecycle, on_basket_executed, on_decision
from trade_integrations.auto_paper.session_store import load_session, save_session, start_session, stop_session
from trade_integrations.monitor.execution_ledger import list_open_entries, record_execution_from_widget

logger = logging.getLogger(__name__)


def _widget_path(widget_id: str) -> Path:
    return Path.home() / ".vibe-trading" / "trade_widgets" / f"{widget_id}.json"


def _load_widget(widget_id: str) -> dict[str, Any] | None:
    path = _widget_path(widget_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _orders_from_widget(widget: dict[str, Any], *, product: str) -> list[dict[str, Any]]:
    for step in widget.get("implementation_steps") or []:
        if step.get("action") != "execute_basket":
            continue
        orders = (step.get("payload") or {}).get("orders") or []
        normalized: list[dict[str, Any]] = []
        for order in orders:
            if not isinstance(order, dict) or not order.get("symbol"):
                continue
            row = dict(order)
            row["product"] = product
            normalized.append(row)
        return normalized
    return []


def _audit_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Embed audit record in MCP tool result for Vibe SSE relay."""
    return {"paper_action": record, "audit_id": record.get("audit_id")}


def start_auto_paper(
    *,
    ticker: str,
    budget_inr: float = 20_000.0,
    watchlist: list[str] | None = None,
    max_daily_loss_inr: float = 2_000.0,
    goal: str | None = None,
    mandate: str | None = None,
    agent_mode: bool = True,
) -> dict[str, Any]:
    symbol = ticker.strip().upper()
    symbols = watchlist or [symbol]
    symbols = [s.strip().upper() for s in symbols if s.strip()]
    if symbol not in symbols:
        symbols.insert(0, symbol)

    client = OpenAlgoClient()
    client.ensure_analyzer_mode()

    session = start_session(budget_inr=budget_inr, watchlist=symbols)
    session["agent_mode"] = agent_mode
    session["autonomous"] = True
    session["goal"] = goal or DEFAULT_GOAL
    session["mandate"] = mandate or f"Paper trade {symbol} options intraday; maximize profit to close."
    session["max_daily_loss_inr"] = max_daily_loss_inr
    session["primary_ticker"] = symbol
    session["lifecycle"] = default_lifecycle()
    save_session(session)

    audit = write_paper_action(
        "session_started",
        detail={"ticker": symbol, "budget_inr": budget_inr, "watchlist": symbols},
    )
    feedback = build_market_feedback(ticker=symbol)

    research_jobs: dict[str, bool] = {}
    try:
        from src.scheduled_research.auto_paper_jobs import ensure_vibe_research_jobs

        research_jobs = ensure_vibe_research_jobs()
    except ImportError:
        pass
    except Exception:
        logger.debug("vibe research jobs registration skipped", exc_info=True)

    return {
        "status": "started",
        "paper_mode": True,
        "autonomous": True,
        "primary_ticker": symbol,
        "watchlist": symbols,
        "budget_inr": budget_inr,
        "agent_mode": agent_mode,
        "goal": session["goal"],
        "market_feedback": feedback,
        "vibe_research_jobs": research_jobs,
        "audit": _audit_payload(audit),
        "next_step": (
            "Autonomous session started. You decide research depth each turn from market feedback. "
            "Goal: risk-adjusted profit by close. record_auto_paper_decision every turn."
        ),
    }


def stop_auto_paper(*, unregister_scheduler: bool = True) -> dict[str, Any]:
    session = stop_session()
    scheduler_removed: dict[str, bool] = {}
    if unregister_scheduler:
        try:
            scheduler_removed = remove_auto_paper_scheduler_jobs()
        except Exception:
            logger.debug("scheduler job cleanup skipped", exc_info=True)
    audit = write_paper_action(
        "session_stopped",
        detail={"stopped_at": session.get("stopped_at"), "scheduler_removed": scheduler_removed},
    )
    return {
        "status": "stopped",
        "stopped_at": session.get("stopped_at"),
        "scheduler_removed": scheduler_removed,
        "audit": _audit_payload(audit),
    }


def get_market_feedback(*, ticker: str | None = None) -> dict[str, Any]:
    session = load_session()
    focus = ticker or session.get("primary_ticker")
    feedback = build_market_feedback(ticker=focus)
    audit = write_paper_action("market_feedback", detail={"summary": feedback.get("summary")})
    feedback["audit"] = _audit_payload(audit)
    return feedback


def get_status() -> dict[str, Any]:
    cfg = get_auto_paper_config()
    session = load_session()
    open_entries = list_open_entries()

    funds: dict[str, Any] | None = None
    analyze_mode: bool | None = None
    try:
        client = OpenAlgoClient()
        funds = client.get_funds()
        analyze_mode = client.analyzer_status()
    except RuntimeError:
        pass

    position_summary = []
    for entry in open_entries:
        position_summary.append(
            {
                "widget_id": entry.get("widget_id"),
                "underlying": entry.get("underlying"),
                "recommended_name": entry.get("recommended_name"),
                "execution_mode": entry.get("execution_mode"),
                "net_max_loss": entry.get("net_max_loss"),
            }
        )

    reconcile = reconcile_paper_state()

    return {
        "session": session_summary_for_status(),
        "market_open": is_market_session_open(cfg),
        "market_window": f"{cfg.market_open}-{cfg.market_close} IST",
        "open_positions": len(open_entries),
        "positions": position_summary,
        "funds": funds,
        "analyze_mode": analyze_mode,
        "halted": bool(session.get("halted")),
        "halt_reason": session.get("halt_reason"),
        "last_tick_at": session.get("last_tick_at"),
        "last_tick": session.get("last_tick"),
        "last_market_feedback": session.get("last_market_feedback"),
        "reconcile": asdict(reconcile),
    }


def execute_basket(widget_id: str) -> dict[str, Any]:
    widget = _load_widget(widget_id)
    if widget is None:
        raise ValueError(f"Widget not found: {widget_id}")

    cfg = get_auto_paper_config()
    orders = _orders_from_widget(widget, product=cfg.product)
    if not orders:
        raise ValueError(f"No execute_basket orders in widget {widget_id}")

    client = OpenAlgoClient()
    if not client.ensure_analyzer_mode():
        raise RuntimeError("Could not enable OpenAlgo analyzer (paper) mode")

    results = client.place_basket(orders, strategy="auto_paper_agent")
    record_execution_from_widget(widget, results, execution_mode="paper")

    session = load_session()
    session["trades_today"] = int(session.get("trades_today") or 0) + 1
    on_basket_executed(
        session,
        widget_id=widget_id,
        strategy=(widget.get("recommended") or {}).get("name"),
        underlying=widget.get("underlying"),
    )
    save_session(session)

    audit = write_paper_action(
        "basket_executed",
        detail={
            "widget_id": widget_id,
            "underlying": widget.get("underlying"),
            "strategy": (widget.get("recommended") or {}).get("name"),
            "orders": len(orders),
        },
    )

    return {
        "status": "executed",
        "widget_id": widget_id,
        "underlying": widget.get("underlying"),
        "strategy": (widget.get("recommended") or {}).get("name"),
        "orders_placed": len(orders),
        "results": results,
        "execution_mode": "paper",
        "audit": _audit_payload(audit),
    }


def record_decision(
    *,
    decision: str,
    rationale: str,
    ticker: str | None = None,
    actions_taken: list[str] | None = None,
) -> dict[str, Any]:
    session = load_session()
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "decision": decision.strip().upper(),
        "rationale": rationale.strip(),
        "ticker": (ticker or session.get("primary_ticker") or "").strip().upper() or None,
        "actions_taken": actions_taken or [],
    }
    decisions = list(session.get("decisions") or [])
    decisions.append(entry)
    session["decisions"] = decisions[-100:]
    session["last_agent_turn_at"] = entry["at"]
    session["last_decision"] = entry
    on_decision(session, decision=entry["decision"], rationale=entry["rationale"], ticker=entry.get("ticker"))
    save_session(session)

    audit = write_paper_action("decision_recorded", detail=entry)
    return {"status": "recorded", "decision": entry, "audit": _audit_payload(audit)}
