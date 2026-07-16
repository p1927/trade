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


def format_research_context_for_agent(
    artifact: dict[str, Any] | None,
    *,
    index_artifact: dict[str, Any] | None = None,
) -> str:
    """Build a hidden context block injected into the Vibe chat agent prompt."""
    parts: list[str] = []
    if artifact and artifact.get("asset_type") != "index":
        parts.append(_format_options_stock_context(artifact))
    if index_artifact:
        parts.append(_format_index_research_context(index_artifact))
    elif artifact and artifact.get("asset_type") == "index":
        parts.append(_format_index_research_context(artifact))
    return "\n\n".join(p for p in parts if p)


def _format_options_stock_context(artifact: dict[str, Any]) -> str:
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
    if has_strategy_options_to_present(artifact):
        lines.append(
            f"When presenting ranked strategy options or the recommended trade plan with legs, "
            f"call OpenAlgo MCP {widget_tool} in the same turn so the user can compare "
            f"alternatives, see payoff/charges, and execute. Do not answer with markdown-only "
            f"strategy lists. If plan_status is stale, use refresh=true."
        )
    else:
        lines.append(
            f"Do not call {widget_tool} for prediction, events, or browse-only answers — "
            f"explain from research_context without the widget until ranked_strategies is "
            f"populated. Refresh the plan first if the user asks which strategy to trade."
        )
    return "\n".join(lines)


def _format_index_research_context(artifact: dict[str, Any]) -> str:
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

    stage_errors = artifact.get("stage_errors") or []
    if stage_errors:
        lines.append(f"stage_errors: {'; '.join(str(e) for e in stage_errors[:3])}")

    lines.append("[/index_research_context]")
    lines.append(
        "For index-level direction, factor attribution, macro overlay, and scenario ranges, "
        "call OpenAlgo MCP get_index_trade_plan(ticker, horizon_days=14) and "
        "get_index_trade_widget(ticker) in the same turn. "
        "Use refresh=true when the user asks for fresh index research or spot moved materially."
    )
    lines.append(
        "MANDATORY: When answering where the index is headed, which factors drive NIFTY, "
        "or index scenarios, you MUST call get_index_trade_widget(ticker) — not markdown-only prose. "
        "For F&O strategy legs after the index view, call get_options_trade_widget(ticker) "
        "only when presenting ranked strategy options from the options hub plan."
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
