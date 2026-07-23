"""Hub research context helpers for Vibe agent prompts and TradingAgents debate."""

from __future__ import annotations

from typing import Any


def normalize_strategy_key(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def has_strategy_options_to_present(artifact: dict[str, Any] | None) -> bool:
    """True when hub research has ranked strategies or a recommended plan with legs."""
    if not artifact:
        return False
    ranked = artifact.get("ranked_strategies") or []
    if ranked:
        return True
    rec = artifact.get("recommended") or {}
    return bool(rec.get("name") and rec.get("legs"))


def infer_debate_asset_type(ticker: str, explicit: str | None = None) -> str:
    """Choose stock vs options context for TradingAgents debate."""
    if explicit in ("options", "stock"):
        return explicit
    key = ticker.strip().upper()
    try:
        from trade_integrations.bridge.agent_debate import is_index_ticker
        from trade_integrations.dataflows.options_research.market import is_options_research_eligible

        if is_index_ticker(key) or is_options_research_eligible(key):
            return "options"
    except Exception:
        if key in {"NIFTY", "NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}:
            return "options"
    return "stock"


def format_debate_context_for_agent(debate_artifact: dict[str, Any] | None) -> str:
    """Build TradingAgents debate block for Vibe agent prompts."""
    if not debate_artifact:
        return ""
    from trade_integrations.research.debate_synthesis import extract_structured_debate

    structured = extract_structured_debate(debate_artifact)
    inv = debate_artifact.get("investment_debate") or {}
    risk = debate_artifact.get("risk_debate") or {}
    lines = [
        "[debate_context]",
        f"ticker: {debate_artifact.get('ticker')}",
        f"rating: {debate_artifact.get('rating')}",
        f"asset_type: {debate_artifact.get('asset_type', 'stock')}",
    ]
    if structured.get("view"):
        lines.append(f"debate_view: {structured['view']}")
    if structured.get("direction_confidence") is not None:
        lines.append(f"debate_confidence: {structured['direction_confidence']}")
    if structured.get("expected_return_pct") is not None:
        lines.append(f"debate_expected_return_pct: {structured['expected_return_pct']}")
    bull = str(inv.get("bull_summary") or "")[:400]
    bear = str(inv.get("bear_summary") or "")[:400]
    if bull:
        lines.append(f"bull_summary: {bull}")
    if bear:
        lines.append(f"bear_summary: {bear}")
    judge = str(inv.get("judge_decision") or "")[:400]
    if judge:
        lines.append(f"investment_judge: {judge}")
    risk_judge = str(risk.get("judge_decision") or "")[:400]
    if risk_judge:
        lines.append(f"risk_judge: {risk_judge}")
    final = str(debate_artifact.get("final_trade_decision") or "")[:500]
    if final:
        lines.append(f"final_trade_decision: {final}")
    lines.append("[/debate_context]")
    lines.append(
        "Reconcile debate_view with [research_context] ranked strategies before finalizing "
        "a trade widget. If debate conflicts with the quant plan, prefer the higher-confidence "
        "signal or call get_*_trade_widget(refresh=true) after explaining the conflict."
    )
    return "\n".join(lines)


def format_research_context_for_agent(
    artifact: dict[str, Any] | None,
    *,
    index_artifact: dict[str, Any] | None = None,
    debate_artifact: dict[str, Any] | None = None,
    widget_intent: str = "none",
    session_config: dict[str, Any] | None = None,
) -> str:
    """Build a hidden context block injected into the Vibe chat agent prompt."""
    parts: list[str] = [f"[widget_intent: {widget_intent}]"]
    if artifact and artifact.get("asset_type") != "index":
        parts.append(_format_options_stock_context(artifact, widget_intent=widget_intent))
    if index_artifact:
        parts.append(_format_index_research_context(index_artifact, widget_intent=widget_intent))
    elif artifact and artifact.get("asset_type") == "index":
        parts.append(_format_index_research_context(artifact, widget_intent=widget_intent))
    debate_block = format_debate_context_for_agent(debate_artifact)
    if debate_block:
        parts.append(debate_block)
    news_block = format_news_scenario_context(session_config)
    if news_block:
        parts.append(news_block)
    paper_block = _format_paper_calibration_context()
    if paper_block:
        parts.append(paper_block)
    return "\n\n".join(p for p in parts if p)


def _format_paper_calibration_context() -> str:
    try:
        from trade_integrations.autonomous_agents.outcome_ledger import (
            compute_execution_calibration_metrics,
            compute_agent_calibration_metrics,
        )

        paper = compute_agent_calibration_metrics()
        execution = compute_execution_calibration_metrics()
    except Exception:
        return ""
    if not paper.get("closed_trades") and not execution.get("closed_trades"):
        return ""
    lines = ["[trade_calibration]"]
    if paper.get("closed_trades"):
        lines.extend(
            [
                f"paper_closed_trades: {paper.get('closed_trades')}",
                f"paper_avg_net_pnl_inr: {paper.get('avg_net_pnl_inr')}",
            ]
        )
        rates = paper.get("strategy_hit_rates") or {}
        if rates:
            lines.append("paper_strategy_hit_rates:")
            for name, hit in sorted(rates.items(), key=lambda kv: kv[1], reverse=True)[:8]:
                lines.append(f"  - {name}: {hit:.0%}")
    if execution.get("closed_trades"):
        lines.extend(
            [
                f"execution_closed_trades: {execution.get('closed_trades')}",
                f"execution_avg_net_pnl_inr: {execution.get('avg_net_pnl_inr')}",
            ]
        )
        rates = execution.get("strategy_hit_rates") or {}
        if rates:
            lines.append("execution_strategy_hit_rates:")
            for name, hit in sorted(rates.items(), key=lambda kv: kv[1], reverse=True)[:8]:
                lines.append(f"  - {name}: {hit:.0%}")
    lines.append("[/trade_calibration]")
    return "\n".join(lines)


def _format_options_stock_context(
    artifact: dict[str, Any],
    *,
    widget_intent: str = "none",
) -> str:
    lines = [
        "[research_context]",
        f"ticker: {artifact.get('underlying') or artifact.get('ticker')}",
        f"asset_type: {artifact.get('asset_type', 'options')}",
        f"plan_status: {artifact.get('plan_status', 'unknown')}",
    ]
    if artifact.get("expiry"):
        lines.append(f"expiry: {artifact['expiry']}")
    if artifact.get("spot"):
        lines.append(f"spot: {artifact['spot']}")

    for warning in artifact.get("data_warnings") or []:
        lines.append(f"warning: {warning}")

    ranked = artifact.get("ranked_strategies") or []
    if ranked:
        lines.append("ranked_strategies:")
        for row in ranked[:5]:
            lines.append(
                f"  - {row.get('name')} (tier={row.get('tier')}, score={row.get('score')})"
            )

    rec = artifact.get("recommended") or {}
    if rec.get("name"):
        rationale = (rec.get("rationale") or "")[:240]
        lines.append(f"recommended: {rec.get('name')} — {rationale}".rstrip(" —"))

    pred = artifact.get("prediction") or {}
    if pred.get("view"):
        lines.append(
            f"prediction: view={pred.get('view')} iv_regime={pred.get('iv_regime')} "
            f"confidence={pred.get('confidence')}"
        )

    stage_errors = artifact.get("stage_errors") or []
    if stage_errors:
        lines.append(f"stage_errors: {'; '.join(str(e) for e in stage_errors[:3])}")

    staleness = artifact.get("staleness") or {}
    if staleness:
        lines.append(f"staleness_status: {staleness.get('status', 'unknown')}")
        reasons = staleness.get("reasons") or []
        if reasons:
            lines.append(f"staleness_reasons: {', '.join(str(r) for r in reasons)}")
        if staleness.get("suggested_action"):
            lines.append(f"suggested_action: {staleness['suggested_action']}")

    lines.append("[/research_context]")
    lines.append(
        "The Research side panel shows the same hub plan. If plan_status is incomplete/partial "
        "or warnings mention the option chain, call OpenAlgo MCP get_options_trade_widget(ticker, "
        "refresh=true) and get_options_trade_plan(ticker, refresh=true) before recommending legs. "
        "For stock underlyings use get_stock_trade_widget / get_stock_trade_plan instead."
    )
    if staleness.get("status") in ("stale", "broken"):
        lines.append(
            "Plan is stale or broken — call get_options_trade_widget(ticker, refresh=true) "
            "and get_options_trade_plan(ticker, refresh=true) before recommending legs."
        )
    asset = artifact.get("asset_type", "options")
    widget_tool = (
        "get_stock_trade_widget(ticker)"
        if asset == "stock"
        else "get_options_trade_widget(ticker)"
    )
    if widget_intent in ("options_strategy", "execute_refresh") and has_strategy_options_to_present(
        artifact
    ):
        refresh_hint = ", refresh=true" if widget_intent == "execute_refresh" else ""
        lines.append(
            f"When presenting ranked strategy options or the recommended trade plan with legs, "
            f"call OpenAlgo MCP {widget_tool}{refresh_hint} in the same turn so the user can "
            f"compare alternatives, see payoff/charges, and execute. Do not answer with "
            f"markdown-only strategy lists."
        )
    elif widget_intent == "stock_trade" and artifact.get("asset_type") == "stock":
        lines.append(
            f"When presenting a stock trade recommendation with entry/exit, call OpenAlgo MCP "
            f"{widget_tool} in the same turn."
        )
    else:
        lines.append(
            f"Do not call {widget_tool} for prediction, events, or browse-only answers — "
            f"explain from research_context without the widget until the user asks for a "
            f"specific strategy or trade plan."
        )
    return "\n".join(lines)


def _format_index_research_context(
    artifact: dict[str, Any],
    *,
    widget_intent: str = "none",
) -> str:
    ticker = artifact.get("underlying") or artifact.get("ticker") or "NIFTY"
    lines = [
        "[index_research_context]",
        f"ticker: {ticker}",
        "asset_type: index",
        f"plan_status: {artifact.get('plan_status', 'unknown')}",
    ]
    if artifact.get("spot"):
        lines.append(f"spot: {artifact['spot']}")

    horizon = artifact.get("horizon") or {}
    if horizon.get("days"):
        lines.append(f"horizon_days: {horizon['days']}")
    if horizon.get("label"):
        lines.append(f"horizon_label: {horizon['label']}")

    for warning in artifact.get("data_warnings") or []:
        lines.append(f"warning: {warning}")

    pred = artifact.get("prediction") or {}
    if pred.get("view"):
        range_block = pred.get("range") if isinstance(pred.get("range"), dict) else {}
        lines.append(
            f"index_prediction: view={pred.get('view')} "
            f"expected_return_pct={pred.get('expected_return_pct')} "
            f"range_low={range_block.get('low')} range_high={range_block.get('high')}"
        )

    regime = artifact.get("regime") or {}
    if regime.get("label"):
        lines.append(f"regime: {regime.get('label')}")

    top_factors = artifact.get("top_factors") or []
    factor_exp = artifact.get("factor_explanation") or {}
    if not top_factors:
        top_factors = factor_exp.get("contributors") or []
    if top_factors:
        lines.append("top_factor_contributors:")
        for row in top_factors[:6]:
            name = row.get("factor") or row.get("name") or "factor"
            share = row.get("share_of_macro") or row.get("contribution_pct")
            pts = row.get("contribution_index_pts")
            lines.append(f"  - {name}: share={share} index_pts={pts}")

    scenarios = artifact.get("scenarios") or []
    if scenarios:
        lines.append("index_scenarios:")
        for row in scenarios[:4]:
            lines.append(
                f"  - {row.get('event')} / {row.get('outcome')} "
                f"prob={row.get('probability')} range={row.get('index_range')}"
            )

    accuracy = artifact.get("accuracy") or {}
    if accuracy.get("direction_hit_rate") is not None:
        lines.append(f"model_direction_hit_rate: {accuracy.get('direction_hit_rate')}")

    pred_block = artifact.get("prediction") or {}
    interpretation = pred_block.get("interpretation") or {}
    if interpretation.get("strategy_context"):
        lines.append(f"strategy_context: {interpretation['strategy_context']}")
    if interpretation.get("technical_interpretation"):
        lines.append(f"technical_interpretation: {interpretation['technical_interpretation']}")
    if interpretation.get("active_strategy_profile"):
        lines.append(f"active_strategy_profile: {interpretation['active_strategy_profile']}")
    if interpretation.get("strategy_when"):
        lines.append(f"strategy_when: {interpretation['strategy_when']}")
    if interpretation.get("strategy_rationale"):
        lines.append(f"strategy_rationale: {interpretation['strategy_rationale']}")
    if interpretation.get("strategy_risks"):
        lines.append(f"strategy_risks: {interpretation['strategy_risks']}")
    if interpretation.get("strategy_options_handoff"):
        lines.append(f"strategy_options_handoff: {interpretation['strategy_options_handoff']}")
    watch = interpretation.get("indicators_to_watch") or []
    if watch:
        lines.append(f"indicators_to_watch: {', '.join(str(w) for w in watch)}")
    risk_notes = interpretation.get("risk_notes") or []
    if risk_notes:
        lines.append("risk_notes:")
        for note in risk_notes[:4]:
            lines.append(f"  - {note}")
    factor_notes = interpretation.get("factor_notes") or {}
    if factor_notes:
        lines.append("factor_notes_with_trust:")
        for key, note in list(factor_notes.items())[:6]:
            lines.append(f"  - {key}: {note}")
    tech_readings = interpretation.get("technical_readings") or {}
    if tech_readings:
        lines.append("technical_readings:")
        for key, value in sorted(tech_readings.items())[:12]:
            lines.append(f"  - {key}: {value}")

    stage_errors = artifact.get("stage_errors") or []
    if stage_errors:
        lines.append(f"stage_errors: {'; '.join(str(e) for e in stage_errors[:3])}")

    lines.append("[/index_research_context]")
    if widget_intent == "index_outlook":
        lines.append(
            "Call OpenAlgo MCP get_index_trade_plan(ticker, horizon_days=14) and "
            "get_index_trade_widget(ticker) when answering index direction, factor "
            "attribution, or scenario ranges. Use refresh=true when the user asks for "
            "fresh index research."
        )
    else:
        lines.append(
            "Do not call get_index_trade_widget for browse-only or generic ticker mentions. "
            "For F&O strategy legs, call get_options_trade_widget(ticker) only when "
            "presenting ranked strategy options from the options hub plan."
        )
    return "\n".join(lines)


def format_news_scenario_context(session_config: dict[str, Any] | None) -> str:
    """Inject news-scenario session binding for Prediction tab advisor."""
    if not session_config:
        return ""
    if str(session_config.get("session_kind") or "") != "news_scenario_advisor":
        return ""
    lines = ["[news_scenario_context]"]
    for key in (
        "pipeline_as_of",
        "pipeline_ticker",
        "horizon_days",
        "date_range",
        "active_draft_id",
        "active_scenario_id",
        "selected_outcome_id",
    ):
        val = session_config.get(key)
        if val is not None and val != "":
            lines.append(f"{key}: {val}")
    lines.extend(
        [
            "policy: use pipeline MCP tools only; never refresh index research; "
            "call save_news_scenario_draft then run_news_event_scenario then get_news_scenario_widget",
            "[/news_scenario_context]",
        ]
    )
    return "\n".join(lines)


def build_tradingagents_options_context(ticker: str, *, asset_type: str = "stock") -> str:
    """Load options hub markdown for TradingAgents past_context injection."""
    if asset_type != "options":
        return ""
    key = ticker.strip().upper()
    try:
        from trade_integrations.context.hub import load_options_research_markdown
        from trade_integrations.dataflows.options_research.market import is_options_research_eligible

        if not is_options_research_eligible(key):
            return ""
        md = load_options_research_markdown(key)
        if not md:
            return ""
        trimmed = md[:7000]
        return (
            f"\n--- F&O trade plan (trade-stack hub: {key}) ---\n"
            f"{trimmed}\n"
            f"--- End F&O trade plan ---\n"
            "News analyst: you may still call get_options_research for updates; "
            "other agents should treat the above as the primary options context.\n"
        )
    except Exception:
        return ""


def build_tradingagents_index_context(ticker: str) -> str:
    """Load index research markdown for TradingAgents past_context injection."""
    key = ticker.strip().upper()
    try:
        from trade_integrations.context.hub import load_index_research_markdown
        from trade_integrations.tools.index_research_tools import is_index_research_eligible

        if not is_index_research_eligible(key):
            return ""
        md = load_index_research_markdown(key)
        if not md:
            return ""
        trimmed = md[:7000]
        return (
            f"\n--- Index research (trade-stack hub: {key}) ---\n"
            f"{trimmed}\n"
            f"--- End index research ---\n"
            "News analyst: call get_index_research for updates; "
            "other agents should treat the above as the primary index-level context.\n"
        )
    except Exception:
        return ""
