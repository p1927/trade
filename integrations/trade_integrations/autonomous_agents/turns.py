"""Turn prompts for autonomous agent watch and full-reasoning ticks."""

from __future__ import annotations

import json
from typing import Any

from trade_integrations.autonomous_agents.agent_learning import read_learning_snapshot
from trade_integrations.autonomous_agents.strategy_progress import format_strategy_progress_for_prompt
from trade_integrations.auto_paper.mandate_config import mandate_config_from_agent
from trade_integrations.auto_paper.strategy_scorer import format_scorer_for_prompt, score_ranked_strategies
from trade_integrations.execution.profile import resolve_profile
from trade_integrations.execution.routing_context import (
    format_advisor_skill_block,
    resolve_agent_routing,
)
from trade_integrations.execution.prompt_fragments import (
    kind_note_for,
    prompt_fragment_for,
    session_header_for,
)

_RUNNING_AGENT_FOOTER = """
## Output format (mandatory — trader-facing)
Respond with this structure only (no audit IDs, no "next-turn expectation", no implementation notes):

## Decision: ENTER | HOLD | SKIP | EXIT | REVISE (confidence N% — below/above gate)
**View:** direction · spot · VIX/regime (cite live tool or hub research)
**Strategy considered:** name (scorer EV if applicable) — chosen or deferred because [reason]
**Watch:** active rules — material alerts since last turn or "none"
**Next action:** what would trigger ENTER or REVISE

## Output rules (mandatory)
- Decide autonomously on this turn — **do not ask the user questions** or offer optional follow-ups.
- Call `record_autonomous_decision` with ENTER | REVISE | EXIT | HOLD | SKIP plus `confidence`, `direction`, and `strategy` when known.
- If below the confidence threshold, record HOLD or SKIP with rationale — do not prompt for permission.
- Use hub research and live tools; cite prediction range and provenance when recommending a strategy.
- **Never** mention: handoff cycle, cached context, synthetic alert, audit pa_, verification reads, idempotent reads.
"""


def _symbols_line(symbols: list[str]) -> str:
    return ", ".join(symbols) if symbols else "NIFTY"


def build_watch_summary_message(*, agent: dict[str, Any], feedback: dict[str, Any]) -> str:
    """Short user-visible watch tick summary (injected before optional full turn)."""
    symbols = list(agent.get("symbols") or [])
    alerts = list(feedback.get("alerts") or [])
    focus = str(feedback.get("focus_ticker") or (symbols[0] if symbols else "NIFTY"))
    alert_text = "; ".join(alerts[:3]) if alerts else "no material alerts"
    return (
        f"[autonomous_watch] {focus} — {alert_text}. "
        f"requires_action={bool(feedback.get('requires_action'))}"
    )


def build_full_reasoning_prompt(*, agent: dict[str, Any], turn_kind: str = "research") -> str:
    """Build a full agent turn prompt for an autonomous instance."""
    profile = resolve_profile(agent=agent)
    routing = resolve_agent_routing(agent)
    symbols = list(agent.get("symbols") or (["SPY"] if profile.is_us else ["NIFTY"]))
    focus = symbols[0]
    constraints = dict(agent.get("constraints") or {})
    mandate = str(agent.get("mandate") or "")
    agent_id = str(agent.get("id") or "")
    threshold = int(constraints.get("confidence_threshold") or 75)
    thesis = dict(agent.get("thesis") or {})
    mc = mandate_config_from_agent(agent)

    learning_snapshot = read_learning_snapshot(agent=agent)
    display_thesis = {**thesis, **dict(learning_snapshot.get("thesis_overlay") or {})}

    thesis_block = ""
    if display_thesis:
        thesis_block = (
            "## Prior thesis\n"
            f"```json\n{json.dumps(display_thesis, indent=2, default=str)}\n```\n"
        )

    guidance_block = ""
    guidance = list(agent.get("user_guidance") or [])[-5:]
    if guidance and not profile.is_us:
        guidance_block = (
            "## User guidance (follow on this turn)\n"
            f"```json\n{json.dumps(guidance, indent=2)}\n```\n"
        )

    learning_block = learning_snapshot.get("prompt_text") or ""

    progress_block = format_strategy_progress_for_prompt(agent=agent, turn_kind=turn_kind)

    scorer_block = ""
    if routing.uses_strategy_scorer:
        tried = list(learning_snapshot.get("tried_strategies") or display_thesis.get("tried_strategies") or [])
        scorer_block = format_scorer_for_prompt(score_ranked_strategies(focus, tried=tried))

    kind_note = kind_note_for(profile.prompt_fragment_id, turn_kind)
    header = session_header_for(profile.market, mode=profile.mode)
    flow = prompt_fragment_for(
        profile.prompt_fragment_id,
        agent_id=agent_id,
        focus=focus,
        threshold=threshold,
        turn_kind=turn_kind,
    )

    market_label = "US (Alpaca paper)" if profile.is_us and profile.is_paper else (
        "US (Alpaca live)" if profile.is_us else "IN (OpenAlgo analyzer)"
    )
    instrument_line = ", ".join(profile.allowed_instruments)

    mandate_details = ""
    if profile.is_us:
        mandate_details = f"- Instrument: **{instrument_line}**\n"
    else:
        budget = float(constraints.get("budget_inr") or 20_000)
        max_loss = float(constraints.get("max_daily_loss_inr") or 2_000)
        mandate_details = (
            f"- Budget: ₹{budget:,.0f} paper | Max daily loss: ₹{max_loss:,.0f}\n"
            f"- Holding: **{mc.holding_period}** | Flatten: **{mc.flatten_policy}** | Product: {mc.resolve_product()}\n"
            f"- Instruments: **{instrument_line}**\n"
        )

    mandate_json = ""
    if not profile.is_us:
        mandate_json = f"\n```json\n{json.dumps(mc.to_dict(), indent=2)}\n```\n"

    title_suffix = " — US / Alpaca" if profile.is_us else ""

    bootstrap_block = ""
    if turn_kind == "bootstrap":
        if routing.research_asset_type == "index" and not profile.is_us:
            research_step = (
                f"2. Call `get_research_status(ticker=\"{focus}\", asset_type=\"index\")` **once**; "
                "if overall `status` is `complete`, proceed to `get_index_trade_plan` — "
                "do not retry because individual stage rows show `complete: false`.\n"
            )
        elif routing.primary_instrument == "equity" and not profile.is_us:
            research_step = (
                f"2. Call `get_research_status(ticker=\"{focus}\", asset_type=\"stock\")` **once**; "
                "if overall `status` is `complete`, proceed to `get_stock_trade_plan` — "
                "do not retry because individual stage rows show `complete: false`.\n"
            )
        elif profile.is_us:
            research_step = (
                f"2. Call `get_stock_browse(\"{focus}\")` and/or `get_us_quote(\"{focus}\")` "
                "for live price context.\n"
            )
        else:
            research_step = (
                f"2. Call `get_research_status(ticker=\"{focus}\", asset_type=\"options\")` **once**; "
                "if overall `status` is `complete`, proceed to `get_options_trade_plan` — "
                "do not retry because individual stage rows show `complete: false`.\n"
            )
        bootstrap_block = (
            "\n## Bootstrap checklist\n"
            "1. Call `get_autonomous_agent_status(agent_id=\""
            f"{agent_id}\")` — confirmed mandate.\n"
            f"{research_step}"
            "3. **One** trade-plan widget for the profile (see Required flow) — never call widget twice.\n"
            "4. `set_agent_watch_spec(agent_id=\""
            f"{agent_id}\", strategy=<chosen_strategy>)` — watchers derived from strategy, not generic mandate dump.\n"
            "5. `record_autonomous_decision` with confidence, direction, strategy — **stop**.\n"
            "6. User approves plan in UI before Nautilus revisions run.\n"
        )

    harness_block = ""
    if agent.get("e2e_harness") and turn_kind == "research" and profile.is_paper:
        if profile.is_us:
            harness_block = (
                "\n## Harness (paper verification)\n"
                f"If flat with no open {focus} position, enter one paper long via the normal Alpaca flow "
                "on this turn, then set watch rules and record the decision.\n"
            )
        elif profile.market == "IN":
            harness_block = (
                "\n## Harness (paper verification)\n"
                f"If flat, enter a paper {focus} "
                f"{'equity' if routing.primary_instrument == 'equity' else 'options'} position "
                "via the normal OpenAlgo basket flow on this turn, then set watch rules and "
                "record the decision.\n"
            )
    elif agent.get("e2e_harness") and turn_kind == "strategy_revision" and profile.is_us and profile.is_paper:
        harness_block = (
            "\n## Harness (paper verification)\n"
            f"If you hold {focus} shares, close the paper position via the normal Alpaca flow "
            "and record EXIT.\n"
        )

    skill_block = format_advisor_skill_block(routing, turn_kind=turn_kind)

    index_flow_note = ""
    if routing.research_asset_type == "index" and not profile.is_us:
        index_flow_note = (
            "\n## Index research flow\n"
            f"For index outlook on **{focus}**, use `get_research_status(ticker=\"{focus}\", "
            "asset_type=\"index\")`, `get_index_trade_plan`, and `get_index_trade_widget`. "
            "Use options plan/widget tools only when recommending concrete F&O legs.\n"
        )

    return f"""# Autonomous agent turn ({turn_kind}){title_suffix}

{header}

{kind_note}

## Mandate
- Agent: **{agent.get('name') or focus}** (`{agent_id}`)
- Symbols: {_symbols_line(symbols)}
- Execution market: **{profile.market}** ({market_label})
- Confidence threshold to act: {threshold}%
{mandate_details}- Mode: {constraints.get('mode') or profile.mode}

{mandate}

{mandate_json}
{thesis_block}{guidance_block}{learning_block}{progress_block}{scorer_block}
{skill_block}{index_flow_note}{bootstrap_block}{flow}
{harness_block}
{_RUNNING_AGENT_FOOTER}"""


def build_orchestrator_system_note() -> str:
    return (
        "You are the autonomous-agent orchestrator. Help the user define focused trading "
        "agents (symbols, mandate, schedules, holding period, alerts). "
        "Policy: (1) If intent is clear, call propose_autonomous_agent immediately with smart "
        "defaults for any omitted fields — the proposal card must be approve-ready. "
        "(2) If symbol, market (IN/US), intraday vs swing, or index instrument type (options vs "
        "directional) is genuinely ambiguous, ask ONE concise question (≤3 bullets or A/B/C) — "
        "then propose on the next turn; do not ask twice. "
        "(3) Plain equity names (RELIANCE, TCS) default to allowed_instruments: [equity] unless "
        "the user mentions options. Explicit options language → [options]. "
        "(4) You MUST call propose_autonomous_agent before ending any turn where the user supplied "
        "enough to propose; never write fake proposal IDs in chat — prose alone does not create cards. "
        "(5) Never execute trades, never discuss live broker setup, never role-play watch ticks or "
        "configure option legs in orchestrator chat. "
        "When status=ready, tell the user to confirm the card. Never commit agents yourself."
    )
