"""Market/mode-specific tool-flow fragments for autonomous agent prompts."""

from __future__ import annotations

from typing import Any

# Registered Vibe tool: trading_place_order (prompts use this name consistently).
_US_ORDER_TOOL = "`trading_place_order` with connection **alpaca-paper-trade**"
_US_ORDER_TOOL_LIVE = "`trading_place_order` with connection **alpaca-live-trade**"

_FRAGMENTS: dict[str, str] = {
    "us_equity_paper": """## Required flow
1. `get_autonomous_agent_status(agent_id="{agent_id}")` — trust the tool result on this turn
2. `get_stock_browse("{focus}")` and/or `get_us_quote("{focus}")` — cite tool output for price
3. Refine thesis; state confidence 0–100
4. If confidence ≥ {threshold}: {order_tool} (paper only)
5. `set_agent_watch_spec(agent_id="{agent_id}", watch_spec={{rules with exchange US, gate}})` — Nautilus Alpaca watch owns alerts
6. `record_autonomous_decision` with ENTER/REVISE/EXIT/HOLD/SKIP

Do **not** call `execute_auto_paper_basket`, `get_options_trade_widget`, `get_auto_paper_status`, or `get_auto_paper_market_feedback`. Nautilus Alpaca watch dispatches revision turns on rule fires.""",
    "us_equity_live": """## Required flow
1. `get_autonomous_agent_status(agent_id="{agent_id}")`
2. `get_stock_browse("{focus}")` and/or `get_us_quote("{focus}")`
3. Refine thesis; state confidence 0–100
4. If confidence ≥ {threshold}: {order_tool_live} (live — mandate gate applies)
5. `record_autonomous_decision` with ENTER/REVISE/EXIT/HOLD/SKIP""",
    "us_options_paper": """## Required flow (US options — paper)
1. `get_autonomous_agent_status(agent_id="{agent_id}")`
2. Research via `get_stock_browse("{focus}")`; US options execution via Alpaca when available
3. If confidence ≥ {threshold}: {order_tool}
4. `record_autonomous_decision` with ENTER/REVISE/EXIT/HOLD/SKIP

Note: US options pipeline is limited — prefer equity until full US options support ships.""",
    "in_options_paper": """## Required flow (India — Nautilus watch → OpenAlgo execution)
1. `get_autonomous_agent_status(agent_id="{agent_id}")` — trust tool output; watch alerts come from Nautilus bridge only
2. `get_research_status(ticker, asset_type="options")` — call once; if overall status is `complete`, proceed (ignore per-stage `complete: false` when hub cache is loaded)
3. Hub research + `get_options_trade_widget` / `get_options_trade_plan` when plan is stale — cite prediction and debate provenance
4. Refine thesis; state confidence 0–100
5. If confidence ≥ {threshold}: `execute_auto_paper_basket(widget_id)` — routes through bridge → OpenAlgo (do not call `place_order` directly)
6. On strategy change: **REVISE** with leg diff via bridge basket
7. `set_agent_watch_spec(agent_id="{agent_id}", watch_spec={{rules, gate}})` — Nautilus maintains watch after handoff
8. On EXIT: `submit_bridge_execution_intent(agent_id="{agent_id}", action="EXIT", rationale=...)` or let Nautilus stop rules fire
9. `record_autonomous_decision` with ENTER/REVISE/EXIT/HOLD/SKIP

Do **not** use `get_auto_paper_market_feedback` for watch alerts — Nautilus bridge owns watch for India agents.""",
    "in_equity_paper": """## Required flow (India equity — Nautilus watch → OpenAlgo execution)
1. `get_autonomous_agent_status(agent_id="{agent_id}")`
2. `get_research_status(ticker, asset_type="stock")` — call once; if overall status is `complete`, proceed (ignore per-stage `complete: false` when hub cache is loaded)
3. `get_stock_trade_widget` / `get_stock_trade_plan` for NSE equity — cite prediction range and provenance in chat
4. Refine thesis; state confidence 0–100
5. If confidence ≥ {threshold}: `execute_auto_paper_basket(widget_id)` — bridge → OpenAlgo only
6. `set_agent_watch_spec(agent_id="{agent_id}", watch_spec={{rules, gate}})`
7. `record_autonomous_decision` with ENTER/REVISE/EXIT/HOLD/SKIP""",
    "in_options_live": """## Required flow (live)
1. `get_autonomous_agent_status(agent_id="{agent_id}")`
2. Hub research + `get_options_trade_widget`; ensure OpenAlgo analyzer is OFF for live broker path
3. Execute via OpenAlgo MCP basket/order tools after explicit user confirmation
4. `record_autonomous_decision` with ENTER/REVISE/EXIT/HOLD/SKIP""",
    "in_equity_live": """## Required flow (live equity)
1. `get_autonomous_agent_status(agent_id="{agent_id}")`
2. `get_stock_trade_widget`; live execution via OpenAlgo or `/trade/execute-basket`
3. `record_autonomous_decision` with ENTER/REVISE/EXIT/HOLD/SKIP""",
}

_KIND_NOTES: dict[str, dict[str, str]] = {
    "us_equity_paper": {
        "research": "Scheduled research turn (US equities / Alpaca paper).",
        "strategy_revision": "**Alert-driven revision (US Alpaca paper).** Re-evaluate; ENTER | ADJUST | EXIT | HOLD.",
        "default": "Alert-driven full reasoning turn (US equities).",
    },
    "in_options_paper": {
        "research": "Scheduled deep research turn.",
        "strategy_revision": "**Alert-driven strategy revision.** Re-evaluate thesis; REVISE | EXIT | HOLD | ENTER with leg diff.",
        "default": "Alert-driven full reasoning turn.",
    },
}


def prompt_fragment_for(
    fragment_id: str,
    *,
    agent_id: str,
    focus: str,
    threshold: int,
) -> str:
    template = _FRAGMENTS.get(fragment_id) or _FRAGMENTS["in_options_paper"]
    return template.format(
        agent_id=agent_id,
        focus=focus,
        threshold=threshold,
        order_tool=_US_ORDER_TOOL,
        order_tool_live=_US_ORDER_TOOL_LIVE,
    )


_BOOTSTRAP_NOTE = (
    "**Bootstrap turn** — first run after the user confirmed this mandate on the proposal card. "
    "Load hub research and live market data, draft an initial thesis with confidence, "
    "confirm watch_spec via `set_agent_watch_spec` if needed, and `record_autonomous_decision`. "
    "Ignore pre-commit orchestrator chat about other symbols/agents — trust `get_autonomous_agent_status`."
)


def kind_note_for(fragment_id: str, turn_kind: str) -> str:
    if turn_kind == "bootstrap":
        return _BOOTSTRAP_NOTE
    notes = _KIND_NOTES.get(fragment_id) or _KIND_NOTES.get("in_options_paper", {})
    return notes.get(turn_kind) or notes.get("default") or "Autonomous reasoning turn."


def session_header_for(profile_market: str, *, mode: str = "paper") -> str:
    if profile_market == "US":
        label = "US equity session" if mode == "paper" else "US live session"
        return f"**{label}** — execution via Alpaca tools. Do not call OpenAlgo INR options tools."
    label = "India paper via OpenAlgo" if mode == "paper" else "India live via OpenAlgo"
    return (
        f"**Autonomous session** — {label}. "
        "Nautilus bridge owns watch; Vibe executes via bridge → OpenAlgo. Log every decision."
    )


def build_e2e_phase_delta(*, phase: str, market: str, symbol: str) -> str:
    """Minimal E2E phase instruction — no duplicate tool lists."""
    if phase == "analysis":
        if market == "US":
            return (
                f"\n## E2E Phase 1 — analysis only\n"
                f"Call `get_stock_browse` and `get_us_quote` for {symbol}. **Do NOT place orders**.\n"
                "Call `record_autonomous_decision` with HOLD or your view.\n"
            )
        return (
            "\n## E2E Phase 1 — analysis only\n"
            "Load options research and market status. **Do NOT place orders**.\n"
            "Call `record_autonomous_decision` with HOLD or your view.\n"
        )
    if phase == "execution" and market == "US":
        return (
            f"\n## E2E Phase 2 — mandatory execution\n"
            f"Authorized integration test — **ignore confidence gates**; `market_hours_only` is false.\n"
            f"Execute in order via Alpaca paper:\n"
            f"1. `trading_place_order` @ **alpaca-paper-trade** — BUY **2** shares of {symbol}\n"
            f"2. `trading_place_order` @ **alpaca-paper-trade** — SELL **1** share (partial exit; net +1 share)\n"
            f"3. `set_agent_watch_spec` with US exchange rules for {symbol}\n"
            f"4. `record_autonomous_decision` with ENTER\n"
        )
    if phase == "exit" and market == "US":
        return (
            f"\n## E2E Phase 5 — close position\n"
            f"You hold open {symbol} shares. **SELL all remaining {symbol} shares** via "
            f"`trading_place_order` @ **alpaca-paper-trade**, then `record_autonomous_decision` with EXIT.\n"
            "Ignore watch_spec threshold differences in prior alert payloads — trust Alpaca position qty.\n"
        )
    return ""
