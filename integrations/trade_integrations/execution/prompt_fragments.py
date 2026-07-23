"""Market/mode-specific tool-flow fragments for autonomous agent prompts."""

from __future__ import annotations

import os
from typing import Any

# Registered Vibe tool: trading_place_order retained for legacy E2E scripts only.
_US_ORDER_TOOL = "`execute_auto_paper_basket(widget_id)` — routes through bridge → OpenAlgo Alpaca plugin"
_US_ORDER_TOOL_LIVE = "`execute_auto_paper_basket(widget_id)` or OpenAlgo MCP basket after user confirmation"

_FRAGMENTS: dict[str, str] = {
    "us_equity_paper": """## Required flow (US — Nautilus watch → OpenAlgo Alpaca plugin)
1. `get_autonomous_agent_status(agent_id="{agent_id}")` — trust the tool result on this turn
2. `get_stock_browse("{focus}")` and/or `get_us_quote("{focus}")` — cite tool output for price
3. Refine thesis; state confidence 0–100
4. If confidence ≥ {threshold}: {order_tool} (paper only)
5. `set_agent_watch_spec(agent_id="{agent_id}", watch_spec={{rules with exchange US, gate}})` — Nautilus OpenAlgo watch owns alerts
6. On EXIT: `submit_bridge_execution_intent(agent_id="{agent_id}", action="EXIT", rationale=...)` or let Nautilus stop rules fire
7. `record_autonomous_decision` with ENTER/REVISE/EXIT/HOLD/SKIP

Do **not** call `get_options_trade_widget` for India-only paths. Nautilus bridge dispatches revision turns on rule fires.""",
    "us_equity_live": """## Required flow
1. `get_autonomous_agent_status(agent_id="{agent_id}")`
2. `get_stock_browse("{focus}")` and/or `get_us_quote("{focus}")`
3. Refine thesis; state confidence 0–100
4. If confidence ≥ {threshold}: {order_tool_live} (live — mandate gate applies)
5. `record_autonomous_decision` with ENTER/REVISE/EXIT/HOLD/SKIP""",
    "us_options_paper": """## Required flow (US options — paper)
1. `get_autonomous_agent_status(agent_id="{agent_id}")`
2. Research via `get_stock_browse("{focus}")`; US paper execution via OpenAlgo (Alpaca plugin)
3. If confidence ≥ {threshold}: {order_tool}
4. `record_autonomous_decision` with ENTER/REVISE/EXIT/HOLD/SKIP

Note: US options pipeline is limited — prefer equity until full US options support ships.""",
    "in_options_paper": """## Required flow (India — Nautilus watch → OpenAlgo execution)
1. `get_autonomous_agent_status(agent_id="{agent_id}")` — trust tool output; watch alerts come from Nautilus bridge only
2. `get_research_status(ticker, asset_type="options")` — call once; if overall status is `complete`, proceed (ignore per-stage `complete: false` when hub cache is loaded)
3. Hub research + `get_options_trade_widget` / `get_options_trade_plan` when plan is stale — cite prediction and debate provenance
4. Refine thesis; state confidence 0–100
5. If confidence ≥ {threshold}: `execute_auto_paper_basket(widget_id)` — routes through bridge → OpenAlgo (do not call `place_order` directly)
6. On strategy change: **REVISE/ADJUST** — bridge builds leg diff from handoff vs widget; expect a **post_execution** turn after fills
7. `set_agent_watch_spec(agent_id="{agent_id}", watch_spec={{rules, gate}})` — Nautilus maintains watch after handoff
8. On EXIT: `submit_bridge_execution_intent(agent_id="{agent_id}", action="EXIT", rationale=...)` or let Nautilus stop rules fire
9. After execution: cite `order_state` from progress snapshot before next REVISE
10. `record_autonomous_decision` with ENTER/REVISE/EXIT/HOLD/SKIP

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
        "research": "Scheduled research turn (US equities / OpenAlgo Alpaca plugin).",
        "strategy_revision": "**Alert-driven revision (US OpenAlgo paper).** Re-evaluate; ENTER | ADJUST | EXIT | HOLD.",
        "default": "Alert-driven full reasoning turn (US equities).",
    },
    "in_options_paper": {
        "research": "Scheduled deep research turn.",
        "strategy_revision": "**Alert-driven strategy revision.** Re-evaluate thesis; REVISE | EXIT | HOLD | ENTER with leg diff.",
        "post_execution": "**Post-execution review.** Confirm fills/order_state; HOLD | REVISE | EXIT; update watchers if levels changed.",
        "default": "Alert-driven full reasoning turn.",
    },
}


def prompt_fragment_for(
    fragment_id: str,
    *,
    agent_id: str,
    focus: str,
    threshold: int,
    turn_kind: str = "research",
) -> str:
    if turn_kind == "bootstrap":
        flow = _bootstrap_flow(fragment_id, agent_id=agent_id, focus=focus, threshold=threshold)
        if isinstance(flow, str) and flow.startswith("##"):
            return flow
    if turn_kind == "strategy_revision":
        return _revision_flow(fragment_id, agent_id=agent_id, focus=focus, threshold=threshold)
    if turn_kind == "research":
        if not _research_on_schedule_enabled():
            return (
                "## Required flow\n"
                "Scheduled research is disabled for autonomous agents. "
                "Do not call trade widget tools. Reply with a one-line ack only.\n"
            )
        return _research_flow(
            fragment_id,
            agent_id=agent_id,
            focus=focus,
            threshold=threshold,
        )
    template = _FRAGMENTS.get(fragment_id) or _FRAGMENTS["in_options_paper"]
    return template.format(
        agent_id=agent_id,
        focus=focus,
        threshold=threshold,
        order_tool=_US_ORDER_TOOL,
        order_tool_live=_US_ORDER_TOOL_LIVE,
    )


_BOOTSTRAP_NOTE = (
    "**Bootstrap turn** — first run after mandate confirm. "
    "Load hub research + live data, emit **one** trade-plan widget, set **strategy-specific** watchers, "
    "record decision, then stop. User must approve the plan before Nautilus revisions run."
)

_REVISION_NOTE = (
    "**Nautilus alert revision** — re-evaluate thesis after a watcher fired. "
    "You may refresh hub research and emit **one** updated trade-plan widget if strategy changed. "
    "Update watchers via `set_agent_watch_spec` with the new strategy name. No user confirmation needed."
)

_RESEARCH_SKIP_NOTE = (
    "**Scheduled research skipped** — autonomous agents only revise on Nautilus watcher alerts. "
    "Do not call trade widget tools on this turn."
)


def kind_note_for(fragment_id: str, turn_kind: str) -> str:
    if turn_kind == "bootstrap":
        return _BOOTSTRAP_NOTE
    if turn_kind == "strategy_revision":
        return _REVISION_NOTE
    if turn_kind == "research":
        if _research_on_schedule_enabled():
            notes = _KIND_NOTES.get(fragment_id) or _KIND_NOTES.get("in_options_paper", {})
            return notes.get("research") or "Scheduled deep research turn."
        return _RESEARCH_SKIP_NOTE
    notes = _KIND_NOTES.get(fragment_id) or _KIND_NOTES.get("in_options_paper", {})
    return notes.get(turn_kind) or notes.get("default") or "Autonomous reasoning turn."


def _bootstrap_flow(fragment_id: str, *, agent_id: str, focus: str, threshold: int = 75) -> str:
    if fragment_id == "in_equity_paper":
        return f"""## Required flow (bootstrap — India equity)
1. `get_autonomous_agent_status(agent_id="{agent_id}")`
2. `get_research_status(ticker="{focus}", asset_type="stock")` — once; proceed when overall status is `complete`
3. **One** `get_stock_trade_widget(ticker="{focus}")` — do not call plan + widget; do not call twice
4. Refine thesis; confidence 0–100
5. `set_agent_watch_spec(agent_id="{agent_id}", strategy=<chosen_strategy_name>)` — backend derives rules from strategy
6. `record_autonomous_decision` with HOLD/SKIP/ENTER — **stop**; user approves plan next"""
    if fragment_id == "in_options_paper":
        return f"""## Required flow (bootstrap — India options)
1. `get_autonomous_agent_status(agent_id="{agent_id}")`
2. `get_research_status(ticker="{focus}", asset_type="options")` — once; proceed when overall status is `complete`
3. **One** `get_options_trade_widget(ticker="{focus}")` — do not call twice
4. Refine thesis; confidence 0–100
5. `set_agent_watch_spec(agent_id="{agent_id}", strategy=<chosen_strategy_name>)` — strategy-specific Nautilus rules
6. `record_autonomous_decision` — **stop**; user approves plan next"""
    if fragment_id.startswith("us_"):
        return f"""## Required flow (bootstrap — US)
1. `get_autonomous_agent_status(agent_id="{agent_id}")`
2. `get_stock_browse("{focus}")` and/or `get_us_quote("{focus}")`
3. Refine thesis; confidence 0–100
4. `set_agent_watch_spec(agent_id="{agent_id}", strategy=<chosen_strategy_name>)`
5. `record_autonomous_decision` — **stop**"""
    template = _FRAGMENTS.get(fragment_id) or _FRAGMENTS["in_options_paper"]
    return template.format(
        agent_id=agent_id,
        focus=focus,
        threshold=threshold,
        order_tool=_US_ORDER_TOOL,
        order_tool_live=_US_ORDER_TOOL_LIVE,
    )


def _research_on_schedule_enabled() -> bool:
    return os.getenv("AUTONOMOUS_RESEARCH_ON_SCHEDULE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _research_flow(fragment_id: str, *, agent_id: str, focus: str, threshold: int) -> str:
    if fragment_id == "in_equity_paper":
        asset = "stock"
        widget = f'`get_stock_trade_widget(ticker="{focus}")`'
        research = f'`get_research_status(ticker="{focus}", asset_type="{asset}")`'
    elif fragment_id == "in_options_paper":
        asset = "options"
        widget = f'`get_options_trade_widget(ticker="{focus}")`'
        research = f'`get_research_status(ticker="{focus}", asset_type="{asset}")`'
    elif fragment_id.startswith("us_"):
        return f"""## Required flow (scheduled research — US)
1. `get_autonomous_agent_status(agent_id="{agent_id}")`
2. `get_stock_browse("{focus}")` and/or `get_us_quote("{focus}")` — refresh live context
3. Refine thesis; state confidence 0–100
4. If strategy changed: update `set_agent_watch_spec` and `record_autonomous_decision` with REVISE | HOLD"""
    else:
        widget = "trade widget tools"
        research = "`get_research_status`"
    return f"""## Required flow (scheduled research)
1. `get_autonomous_agent_status(agent_id="{agent_id}")`
2. {research} — refresh hub when stale; proceed when overall status is `complete`
3. {widget} once — refresh plan/charges if strategy or legs changed
4. Refine thesis; state confidence 0–100
5. If strategy changed: `set_agent_watch_spec(agent_id="{agent_id}", strategy=<chosen_strategy_name>)`
6. `record_autonomous_decision` with REVISE | HOLD | ENTER (confidence ≥ {threshold} for entry)"""


def _revision_flow(fragment_id: str, *, agent_id: str, focus: str, threshold: int) -> str:
    if fragment_id == "in_equity_paper":
        widget = f'`get_stock_trade_widget(ticker="{focus}")`'
    elif fragment_id == "in_options_paper":
        widget = f'`get_options_trade_widget(ticker="{focus}")`'
    else:
        widget = "live quote tools"
    return f"""## Required flow (Nautilus revision)
1. `get_autonomous_agent_status(agent_id="{agent_id}")`
2. Re-evaluate alert + prior thesis
3. If strategy changed: {widget} once + `set_agent_watch_spec(strategy=<new_strategy>)`
4. `record_autonomous_decision` with REVISE | EXIT | HOLD | ENTER (confidence ≥ {threshold} for entry)"""


def session_header_for(profile_market: str, *, mode: str = "paper") -> str:
    if profile_market == "US":
        label = "US equity session" if mode == "paper" else "US live session"
        return (
            f"**{label}** — execution via OpenAlgo Alpaca broker plugin + Nautilus bridge. "
            "Do not call OpenAlgo INR options tools."
        )
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
            f"Execute in order via OpenAlgo (Alpaca plugin):\n"
            f"1. `execute_auto_paper_basket` — BUY **2** shares of {symbol}\n"
            f"2. Bridge EXIT intent or basket adjust — SELL **1** share (partial exit; net +1 share)\n"
            f"3. `set_agent_watch_spec` with US exchange rules for {symbol}\n"
            f"4. `record_autonomous_decision` with ENTER\n"
        )
    if phase == "exit" and market == "US":
        return (
            f"\n## E2E Phase 5 — close position\n"
            f"You hold open {symbol} shares. **Flatten via `submit_bridge_execution_intent` (EXIT)** "
            f"or bridge stop rules, then `record_autonomous_decision` with EXIT.\n"
            "Trust OpenAlgo positionbook for remaining qty.\n"
        )
    return ""
