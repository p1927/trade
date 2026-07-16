"""Vibe Trading tools catalog for autonomous paper trading (agent decides depth)."""

from __future__ import annotations

INDEX_UNDERLYINGS = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"})


def is_index_underlying(ticker: str) -> bool:
    return ticker.strip().upper() in INDEX_UNDERLYINGS


def build_tools_catalog(*, focus: str, watchlist: list[str]) -> str:
    """Available Vibe + OpenAlgo tools — agent picks what it needs each turn."""
    index_tools = ""
    if is_index_underlying(focus):
        index_tools = """
**Index:** `get_index_trade_plan`, `get_index_trade_widget`
"""

    watch = ", ".join(watchlist)
    return f"""
## Your toolkit (Vibe Trading + OpenAlgo MCP)

Hub research prefetches automatically each turn. Use any tools below as you see fit.

**Session & P&L:** `get_auto_paper_status`, `get_auto_paper_market_feedback`
**Open positions:** `get_plan_position_status`, `close_all_positions`
**Options research:** `get_options_trade_plan`, `get_options_trade_widget`, `get_options_browse`, `get_option_chain`
{index_tools}**Technicals:** `get_trend_snapshot`, `get_momentum_snapshot`, `get_volatility_snapshot`, `get_support_resistance`
**Validation:** `calculate_margin`, `get_trade_charges`, `get_strategy_payoff`
**Debate:** `run_tradingagents_analysis`
**Execute (paper):** `execute_auto_paper_basket`
**Log:** `record_auto_paper_decision` (ENTER / EXIT / HOLD / SKIP — every turn)

Watchlist: {watch} | Focus: {focus}
"""


def build_turn_guidance(*, market_feedback: dict) -> str:
    """Light guidance — agent decides research depth from session state."""
    hint = str(market_feedback.get("research_depth_hint") or "agent_decides")
    eod = market_feedback.get("eod_evaluation") or {}
    pnl = market_feedback.get("session_pnl") or {}

    lines = [
        "## How to use this turn",
        "",
        "You are autonomous. The user is not available. **Goal: maximize risk-adjusted paper profit by session close.**",
        "",
        "You decide how much research to run:",
        "- **Light check** — status + market feedback only; hold or minor action if thesis intact and nothing material changed.",
        "- **Targeted refresh** — one or two tools (e.g. chain, trend, or plan refresh) when drift/news/staleness flags appear.",
        "- **Full research** — plan + widget + margin/charges (+ debate if entering fresh) when flat, re-entering, or thesis unclear.",
        "",
        f"**This turn hint:** `{hint}` (suggestion only — override if you disagree).",
    ]

    if pnl:
        lines.append(
            f"**Session P&L:** started ₹{pnl.get('starting_inr', '?')} → now ₹{pnl.get('current_inr', '?')} "
            f"(Δ {pnl.get('change_since_last_inr', '?')} since last turn, day P&L {pnl.get('day_pnl_inr', '?')})."
        )

    if eod.get("active"):
        lines.append(
            f"**End-of-day review:** {eod.get('minutes_to_close', '?')} min to close — "
            "summarize day performance, whether research served you well, and flatten or hold into close as you judge best."
        )

    lines.extend(
        [
            "",
            "Always `record_auto_paper_decision` before finishing. Paper only — never live.",
            "Respect budget, max daily loss, and lifecycle Plan B / tried strategies when re-entering.",
        ]
    )
    return "\n".join(lines)


def paper_session_vibe_config(*, ticker: str, watchlist: list[str] | None = None) -> dict:
    """Session config for Vibe SessionService."""
    symbols = watchlist or [ticker]
    return {
        "auto_paper_agent_turn": True,
        "autonomous": True,
        "primary_ticker": ticker.strip().upper(),
        "watchlist": [s.strip().upper() for s in symbols if s.strip()],
        "include_shell_tools": False,
        "options_advisor_autonomous": True,
    }
