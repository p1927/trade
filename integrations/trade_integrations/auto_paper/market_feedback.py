"""Market change feedback injected into autonomous agent turns."""

from __future__ import annotations

import json
import os
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from trade_integrations.auto_paper.config import get_auto_paper_config
from trade_integrations.auto_paper.engine import is_market_session_open
from trade_integrations.auto_paper.session_store import load_session, save_session
from trade_integrations.context.hub import load_options_research_json
from trade_integrations.monitor.execution_ledger import (
    fetch_position_book,
    list_open_entries,
    match_positions_for_entry,
)
from trade_integrations.monitor.live_quotes import fetch_underlying_ltp
from trade_integrations.monitor.news_watcher import check_material_news
from trade_integrations.monitor.service import MonitorService
from trade_integrations.monitor.thesis_break import evaluate_thesis_break

IST = ZoneInfo("Asia/Kolkata")


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _minutes_to_session_close(cfg) -> int | None:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return None
    end = _parse_hhmm(cfg.market_close)
    close_dt = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if now.time() > end:
        return 0
    return max(0, int((close_dt - now).total_seconds() / 60))


def _session_pnl_block(session: dict[str, Any]) -> dict[str, Any]:
    """Paper sandbox P&L vs session start and vs last turn."""
    block: dict[str, Any] = {}
    try:
        from trade_integrations.auto_paper.openalgo_client import OpenAlgoClient

        funds = OpenAlgoClient().get_funds()
        available = funds.get("availablecash") or funds.get("available_balance")
        if available is not None:
            current = float(available)
            block["current_inr"] = round(current, 2)
            starting = session.get("starting_balance")
            if starting is None:
                session["starting_balance"] = current
                block["starting_inr"] = round(current, 2)
            else:
                block["starting_inr"] = round(float(starting), 2)
                block["day_pnl_inr"] = round(current - float(starting), 2)
            prior = session.get("last_balance_snapshot")
            if prior is not None:
                try:
                    block["change_since_last_inr"] = round(current - float(prior), 2)
                except (TypeError, ValueError):
                    pass
            session["last_balance_snapshot"] = current
    except Exception:
        pass
    return block


def _research_depth_hint(
    *,
    alerts: list[str],
    positions: list[dict[str, Any]],
    tickers: list[dict[str, Any]],
    minutes_to_close: int | None,
) -> str:
    if minutes_to_close is not None and minutes_to_close <= 30:
        return "eod_review"
    if alerts:
        if any("THESIS BREAK" in a for a in alerts):
            return "full_research"
        if any("drift" in a.lower() or "stale" in a.lower() or "broken" in a.lower() for a in alerts):
            return "targeted_refresh"
        return "targeted_refresh"
    if not positions:
        return "full_research"
    return "light_check"


def _spot_drift_pct(plan_spot: float | None, live_spot: float | None) -> float | None:
    if plan_spot is None or live_spot is None or plan_spot <= 0:
        return None
    return round(abs(live_spot - plan_spot) / plan_spot * 100.0, 2)


def build_market_feedback(*, ticker: str | None = None) -> dict[str, Any]:
    """Snapshot what changed in the market since the last agent turn."""
    cfg = get_auto_paper_config()
    session = load_session()
    watchlist = session.get("watchlist") or list(cfg.watchlist)
    symbols = [str(t).strip().upper() for t in watchlist if str(t).strip()]
    focus = (ticker or session.get("primary_ticker") or symbols[0] if symbols else "NIFTY").upper()

    prior_snapshot = session.get("last_market_snapshot") or {}
    now_iso = datetime.now(timezone.utc).isoformat()

    tickers_block: list[dict[str, Any]] = []
    alerts: list[str] = []

    for symbol in symbols:
        live_spot = fetch_underlying_ltp(symbol)
        doc = load_options_research_json(symbol)
        plan_spot = None
        prediction_view = None
        if doc is not None:
            plan_spot = getattr(doc, "spot", None)
            if plan_spot is None and isinstance(doc, dict):
                plan_spot = doc.get("spot")
            pred = getattr(doc, "prediction", None) or (doc.get("prediction") if isinstance(doc, dict) else {})
            if isinstance(pred, dict):
                prediction_view = pred.get("view")

        drift = _spot_drift_pct(
            float(plan_spot) if plan_spot is not None else None,
            live_spot,
        )
        staleness = MonitorService().evaluate_ticker(symbol)
        since = MonitorService._news_since(symbol)
        news = check_material_news(symbol, since)

        block: dict[str, Any] = {
            "ticker": symbol,
            "live_spot": live_spot,
            "plan_spot": plan_spot,
            "spot_drift_pct": drift,
            "prediction_view": prediction_view,
            "plan_status": staleness.status if staleness else None,
            "plan_staleness_reasons": list(staleness.reasons or []) if staleness else [],
            "material_news_count": len(news),
            "material_news_headlines": [h.get("title") for h in news[:3] if isinstance(h, dict)],
        }
        tickers_block.append(block)

        if drift is not None and drift >= float(os.getenv("OPTIONS_MONITOR_SPOT_DRIFT_PCT", "1.5")):
            alerts.append(f"{symbol} spot drift {drift}% vs plan")
        if news:
            alerts.append(f"{symbol} material news ({len(news)} headlines)")
        if staleness and staleness.status in {"stale", "broken"}:
            alerts.append(f"{symbol} plan {staleness.status}: {', '.join(staleness.reasons or [])}")

    positions_block: list[dict[str, Any]] = []
    position_book = fetch_position_book()
    for entry in list_open_entries():
        underlying = str(entry.get("underlying") or "").upper()
        live_spot = fetch_underlying_ltp(underlying) if underlying else None
        doc = load_options_research_json(underlying) if underlying else None
        _, position_pnl = match_positions_for_entry(entry, position_book)
        thesis = evaluate_thesis_break(doc, entry, live_spot=live_spot, position_pnl=position_pnl)
        row = {
            "widget_id": entry.get("widget_id"),
            "underlying": underlying,
            "strategy": entry.get("recommended_name"),
            "position_pnl": position_pnl,
            "thesis_broken": thesis.broken,
            "thesis_reasons": thesis.reasons,
        }
        positions_block.append(row)
        if thesis.broken:
            alerts.append(
                f"THESIS BREAK {underlying} ({entry.get('widget_id')}): {'; '.join(thesis.reasons)}"
            )

    deltas: dict[str, Any] = {}
    prior_spots = (prior_snapshot.get("tickers") or {}) if isinstance(prior_snapshot, dict) else {}
    for block in tickers_block:
        sym = block["ticker"]
        prev = prior_spots.get(sym, {}) if isinstance(prior_spots, dict) else {}
        prev_spot = prev.get("live_spot") if isinstance(prev, dict) else None
        cur_spot = block.get("live_spot")
        if prev_spot is not None and cur_spot is not None:
            try:
                move = float(cur_spot) - float(prev_spot)
                deltas[sym] = {"spot_move_since_last_turn": round(move, 2)}
            except (TypeError, ValueError):
                pass

    feedback = {
        "generated_at": now_iso,
        "focus_ticker": focus,
        "market_open": is_market_session_open(cfg),
        "alerts": alerts,
        "requires_action": bool(alerts) or not positions_block,
        "tickers": tickers_block,
        "open_positions": positions_block,
        "deltas_since_last_turn": deltas,
        "prior_turn_at": session.get("last_agent_turn_at"),
        "summary": _feedback_summary(alerts, positions_block, tickers_block, focus),
    }

    session_pnl = _session_pnl_block(session)
    if session_pnl:
        feedback["session_pnl"] = session_pnl

    minutes_left = _minutes_to_session_close(cfg)
    feedback["research_depth_hint"] = _research_depth_hint(
        alerts=alerts,
        positions=positions_block,
        tickers=tickers_block,
        minutes_to_close=minutes_left,
    )
    if minutes_left is not None and minutes_left <= 45:
        feedback["eod_evaluation"] = {
            "active": True,
            "minutes_to_close": minutes_left,
            "note": "Evaluate day P&L vs goal; decide flatten, hold, or last trade.",
        }

    session["last_market_snapshot"] = {
        "at": now_iso,
        "tickers": {b["ticker"]: {"live_spot": b.get("live_spot")} for b in tickers_block},
    }
    session["last_market_feedback"] = feedback
    save_session(session)
    return feedback


def _feedback_summary(
    alerts: list[str],
    positions: list[dict[str, Any]],
    tickers: list[dict[str, Any]],
    focus: str,
) -> str:
    parts: list[str] = []
    focus_row = next((t for t in tickers if t.get("ticker") == focus), tickers[0] if tickers else None)
    if focus_row:
        parts.append(
            f"{focus} spot {focus_row.get('live_spot')} "
            f"(plan {focus_row.get('plan_spot')}, drift {focus_row.get('spot_drift_pct')}%)"
        )
    if positions:
        for p in positions:
            pnl = p.get("position_pnl")
            parts.append(
                f"Open {p.get('underlying')} {p.get('strategy')}: P&L {pnl}, "
                f"thesis_broken={p.get('thesis_broken')}"
            )
    else:
        parts.append("Flat — no open paper positions")
    if alerts:
        parts.append("Alerts: " + "; ".join(alerts[:5]))
    return " | ".join(parts)


def format_feedback_for_prompt(feedback: dict[str, Any]) -> str:
    """Compact markdown block for agent turn prompt injection."""
    return (
        "## Market feedback (since last turn)\n"
        f"{feedback.get('summary', '')}\n\n"
        "```json\n"
        f"{json.dumps(feedback, indent=2, default=str)}\n"
        "```\n"
    )
