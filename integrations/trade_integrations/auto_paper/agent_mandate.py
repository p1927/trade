"""Agent mandate and turn prompts for active intraday paper trading."""

from __future__ import annotations

import json
from typing import Any

from trade_integrations.auto_paper.config import get_auto_paper_config
from trade_integrations.auto_paper.lifecycle import format_lifecycle_for_prompt, load_lifecycle, sync_lifecycle_from_positions
from trade_integrations.auto_paper.market_feedback import build_market_feedback, format_feedback_for_prompt
from trade_integrations.auto_paper.reconcile import reconcile_paper_state
from trade_integrations.auto_paper.session_store import load_session, save_session
from trade_integrations.auto_paper.vibe_research import build_tools_catalog, build_turn_guidance


DEFAULT_GOAL = "Maximize risk-adjusted paper profit by session close."

AUTONOMOUS_MODE_NOTICE = """
**Autonomous paper session.** The user is not available. You trade alone until session close or halt.
No confirmation required for paper orders. Log every turn with `record_auto_paper_decision`.
"""


def build_agent_turn_prompt(
    *,
    ticker: str | None = None,
    reconcile_report: dict[str, Any] | None = None,
    market_feedback: dict[str, Any] | None = None,
) -> str:
    """Build the prompt for one active agent trading turn."""
    cfg = get_auto_paper_config()
    session = load_session()
    watchlist = session.get("watchlist") or list(cfg.watchlist)
    budget = float(session.get("budget_inr") or cfg.budget_inr)
    goal = str(session.get("goal") or DEFAULT_GOAL)
    max_loss = float(session.get("max_daily_loss_inr") or cfg.max_daily_loss_inr)
    focus = ticker or (watchlist[0] if watchlist else "NIFTY")

    if market_feedback is None:
        market_feedback = build_market_feedback(ticker=focus)
    feedback_block = format_feedback_for_prompt(market_feedback)

    urgent_block = ""
    urgent = list(session.get("urgent_alerts") or [])
    if urgent:
        urgent_block = "## URGENT alerts (act this turn)\n```json\n" + json.dumps(urgent, indent=2) + "\n```\n"
        session["urgent_alerts"] = []
        from trade_integrations.auto_paper.session_store import save_session

        save_session(session)

    reconcile_block = ""
    if reconcile_report is not None:
        reconcile_block = (
            "## Pre-turn reconcile\n"
            f"```json\n{json.dumps(reconcile_report, indent=2, default=str)}\n```\n"
        )

    sync_lifecycle_from_positions(session)
    lifecycle = load_lifecycle(session)
    lifecycle_block = format_lifecycle_for_prompt(lifecycle)
    save_session(session)

    memory_block = ""
    decisions = list(session.get("decisions") or [])[-5:]
    if decisions:
        memory_block = "## Session memory (recent decisions)\n```json\n" + json.dumps(decisions, indent=2) + "\n```\n"

    tools_block = build_tools_catalog(focus=focus, watchlist=list(watchlist))
    guidance_block = build_turn_guidance(market_feedback=market_feedback)

    return f"""# Paper trading turn

{AUTONOMOUS_MODE_NOTICE}

## Mandate
- Focus: **{focus}** | Watchlist: {", ".join(watchlist)}
- Budget: ₹{budget:,.0f} paper | Max daily loss: ₹{max_loss:,.0f}
- Window: {cfg.market_open}–{cfg.market_close} IST
- Goal: {goal}

{feedback_block}
{urgent_block}
{reconcile_block}
{lifecycle_block}
{memory_block}
{guidance_block}
{tools_block}
"""


def build_thesis_break_prompt(*, ticker: str, widget_id: str, reasons: list[str]) -> str:
    """Focused prompt when position monitor detects thesis break."""
    reason_text = "; ".join(reasons) or "thesis break"
    return f"""# URGENT: Thesis break on open paper position

Autonomous paper session — **no user confirmation required**.

- Underlying: **{ticker}**
- Widget: `{widget_id}`
- Break reasons: {reason_text}

1. `get_auto_paper_market_feedback(ticker="{ticker}")`
2. `get_plan_position_status("{widget_id}")`
3. Decide: **EXIT** (close_all_positions) or **HOLD** with strong rationale
4. `record_auto_paper_decision` with EXIT or HOLD
5. If exited, consider fresh entry only if market feedback supports a new strategy
"""


def is_agent_session_active() -> bool:
    session = load_session()
    if not session.get("enabled"):
        return False
    if session.get("halted"):
        return False
    return bool(session.get("agent_mode", True))


def session_summary_for_status() -> dict[str, Any]:
    session = load_session()
    cfg = get_auto_paper_config()
    return {
        "enabled": bool(session.get("enabled")),
        "agent_mode": bool(session.get("agent_mode", True)),
        "autonomous": bool(session.get("autonomous", True)),
        "vibe_session_id": session.get("vibe_session_id"),
        "goal": session.get("goal") or DEFAULT_GOAL,
        "mandate": session.get("mandate"),
        "watchlist": session.get("watchlist") or list(cfg.watchlist),
        "budget_inr": float(session.get("budget_inr") or cfg.budget_inr),
        "max_daily_loss_inr": float(session.get("max_daily_loss_inr") or cfg.max_daily_loss_inr),
        "started_at": session.get("started_at"),
        "last_agent_turn_at": session.get("last_agent_turn_at"),
        "last_market_feedback": session.get("last_market_feedback"),
        "decisions": (session.get("decisions") or [])[-10:],
        "lifecycle": session.get("lifecycle"),
    }
