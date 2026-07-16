"""Hub research context helpers for Vibe agent prompts and TradingAgents debate."""

from __future__ import annotations

from typing import Any


def normalize_strategy_key(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


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


def format_research_context_for_agent(artifact: dict[str, Any] | None) -> str:
    """Build a hidden context block injected into the Vibe chat agent prompt."""
    if not artifact:
        return ""

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

    lines.append("[/research_context]")
    lines.append(
        "The Research side panel shows the same hub plan. If plan_status is incomplete/partial "
        "or warnings mention the option chain, call OpenAlgo MCP get_options_trade_widget(ticker, "
        "refresh=true) and get_options_trade_plan(ticker, refresh=true) before recommending legs. "
        "For stock underlyings use get_stock_trade_widget / get_stock_trade_plan instead."
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
