"""Agent mandate and turn prompts for active intraday paper trading."""

from __future__ import annotations

import json
from typing import Any

from dataclasses import asdict

from trade_integrations.auto_paper.config import get_auto_paper_config
from trade_integrations.auto_paper.lifecycle import format_lifecycle_for_prompt, load_lifecycle, sync_lifecycle_from_positions
from trade_integrations.auto_paper.market_feedback import build_market_feedback, format_feedback_for_prompt
from trade_integrations.auto_paper.reconcile import reconcile_paper_state
from trade_integrations.auto_paper.session_store import load_session, save_session
from trade_integrations.auto_paper.mandate_config import mandate_config_from_session
from trade_integrations.auto_paper.reflection import format_reflections_for_prompt
from trade_integrations.auto_paper.strategy_scorer import format_scorer_for_prompt, score_ranked_strategies


DEFAULT_GOAL = "Maximize total paper profit today — you have one trading day only."

DEFAULT_MANDATE = (
    "Intraday options trader: enter and exit all positions the same session day; "
    "no overnight carry; maximize profit by market close."
)

DEFAULT_AUTONOMOUS_KICKOFF = """Paper trade {ticker} autonomously until market close. Budget ₹{budget_inr:,}. Sandbox only — never live.

You are an intraday trader with one day to maximize profits. All positions must be opened and closed today — no overnight carry. Your job is to maximize total paper profit by session close.

Start the autonomous paper session now and take the first trading turn. On each scheduler turn, check market feedback and session P&L — decide yourself whether you need a light check, targeted refresh, or full research before acting. Use whatever Vibe and OpenAlgo tools help you maximize same-day profit. Log every turn with record_auto_paper_decision."""

AUTONOMOUS_MODE_NOTICE = """
**Autonomous paper session.** Follow the user's mandate and mandate_config.
No confirmation required for paper orders. Log every turn with `record_auto_paper_decision`.
"""

from trade_integrations.auto_paper.vibe_research import build_tools_catalog, build_turn_guidance


def _mandate_notice(session: dict[str, Any]) -> str:
    mc = mandate_config_from_session(session)
    if session.get("mandate_config") or session.get("mandate"):
        return (
            f"\n**Autonomous paper session** — holding: `{mc.holding_period}`, "
            f"flatten: `{mc.flatten_policy}`, product: `{mc.resolve_product()}`.\n"
            "No live orders. Log every turn with `record_auto_paper_decision`.\n"
        )
    return AUTONOMOUS_MODE_NOTICE


def _mandate_block(session: dict[str, Any], *, budget: float, max_loss: float, cfg) -> str:
    mc = mandate_config_from_session(session)
    goal = str(session.get("goal") or DEFAULT_GOAL)
    mandate_text = str(session.get("mandate") or DEFAULT_MANDATE)
    return f"""## Mandate
- Budget: ₹{budget:,.0f} paper | Max daily loss: ₹{max_loss:,.0f}
- Holding period: **{mc.holding_period}** | Flatten policy: **{mc.flatten_policy}**
- Product: {mc.resolve_product()} | Market hours only: {mc.market_hours_only}
- Confidence threshold: {mc.confidence_threshold}%
- Window (India default): {cfg.market_open}–{cfg.market_close} IST
- Goal: {goal}

{mandate_text}

```json
{json.dumps(mc.to_dict(), indent=2)}
```
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
    reflection_block = format_reflections_for_prompt(limit=2)
    tried = list(lifecycle.get("tried_strategies") or [])
    scorer_block = format_scorer_for_prompt(score_ranked_strategies(focus, tried=tried))

    notice = _mandate_notice(session)
    mandate_block = _mandate_block(session, budget=budget, max_loss=max_loss, cfg=cfg)

    return f"""# Paper trading turn

{notice}

{mandate_block}
{feedback_block}
{urgent_block}
{reconcile_block}
{lifecycle_block}
{scorer_block}
{memory_block}
{reflection_block}
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


def build_resume_prompt(
    *,
    ticker: str | None = None,
    crash_note: str | None = None,
) -> str:
    """Prompt to continue autonomous paper trading after API crash or restart."""
    session = load_session()
    cfg = get_auto_paper_config()
    focus = ticker or session.get("primary_ticker") or (session.get("watchlist") or ["NIFTY"])[0]
    feedback = build_market_feedback(ticker=focus)
    reconcile = reconcile_paper_state()
    sync_lifecycle_from_positions(session)
    lifecycle = load_lifecycle(session)
    save_session(session)

    open_entries = []
    try:
        from trade_integrations.monitor.execution_ledger import list_open_entries

        open_entries = list_open_entries()
    except Exception:
        pass

    crash_block = ""
    if crash_note:
        crash_block = f"## Recovery note\n{crash_note}\n"

    last_decision = session.get("last_decision")
    decision_block = ""
    if last_decision:
        decision_block = (
            "## Last recorded decision\n"
            f"```json\n{json.dumps(last_decision, indent=2, default=str)}\n```\n"
        )

    positions_block = ""
    if open_entries:
        positions_block = (
            "## Open paper positions\n"
            f"```json\n{json.dumps(open_entries, indent=2, default=str)}\n```\n"
        )

    halted_block = ""
    if session.get("halted"):
        halted_block = (
            f"**Session halted:** {session.get('halt_reason') or 'unknown'}. "
            "Do not enter new trades until halt is cleared.\n"
        )

    return f"""# Resume autonomous paper trading

{AUTONOMOUS_MODE_NOTICE}

The prior agent turn may have been interrupted (API restart or crash). **Continue from current broker state** — do not assume the last tool call completed.

{crash_block}{halted_block}
## Mandate
- Focus: **{focus}** | Budget: ₹{float(session.get('budget_inr') or cfg.budget_inr):,.0f} paper
- Goal: {session.get('goal') or DEFAULT_GOAL}

{format_feedback_for_prompt(feedback)}
## Pre-turn reconcile
```json
{json.dumps(asdict(reconcile), indent=2, default=str)}
```
{format_lifecycle_for_prompt(lifecycle)}
{decision_block}
{positions_block}
{build_turn_guidance(market_feedback=feedback)}
{build_tools_catalog(focus=focus, watchlist=list(session.get('watchlist') or cfg.watchlist))}

**First actions this turn:**
1. `get_auto_paper_status()` — confirm session, funds, open positions
2. Reconcile any in-flight orders vs ledger
3. Decide HOLD / EXIT / new ENTER based on market feedback
4. `record_auto_paper_decision` before ending the turn
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
        "mandate_config": session.get("mandate_config"),
        "autonomous_agent_id": session.get("autonomous_agent_id"),
        "watchlist": session.get("watchlist") or list(cfg.watchlist),
        "budget_inr": float(session.get("budget_inr") or cfg.budget_inr),
        "max_daily_loss_inr": float(session.get("max_daily_loss_inr") or cfg.max_daily_loss_inr),
        "started_at": session.get("started_at"),
        "last_agent_turn_at": session.get("last_agent_turn_at"),
        "last_market_feedback": session.get("last_market_feedback"),
        "decisions": (session.get("decisions") or [])[-10:],
        "lifecycle": session.get("lifecycle"),
    }
