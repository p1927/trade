"""Markdown formatter for TradingAgents debate summaries saved to the hub."""

from __future__ import annotations


def format_agent_debate_report(payload: dict) -> str:
    """Render hub agent_debate JSON as readable markdown."""
    ticker = payload.get("ticker") or "?"
    trade_date = payload.get("trade_date") or ""
    rating = payload.get("rating") or ""
    lines = [
        f"# Agent debate · {ticker}",
        "",
        f"**Date:** {trade_date}  ",
        f"**Rating:** {rating}" if rating else "",
        "",
    ]

    debate = payload.get("investment_debate") or {}
    if any(debate.get(k) for k in ("bull_summary", "bear_summary", "judge_decision")):
        lines.extend(["## Investment debate", ""])
        if debate.get("bull_summary"):
            lines.extend(["### Bull", "", str(debate["bull_summary"]), ""])
        if debate.get("bear_summary"):
            lines.extend(["### Bear", "", str(debate["bear_summary"]), ""])
        if debate.get("judge_decision"):
            lines.extend(["### Manager decision", "", str(debate["judge_decision"]), ""])

    risk = payload.get("risk_debate") or {}
    if any(risk.get(k) for k in ("aggressive_summary", "conservative_summary", "neutral_summary", "judge_decision")):
        lines.extend(["## Risk debate", ""])
        if risk.get("aggressive_summary"):
            lines.extend(["### Aggressive", "", str(risk["aggressive_summary"]), ""])
        if risk.get("conservative_summary"):
            lines.extend(["### Conservative", "", str(risk["conservative_summary"]), ""])
        if risk.get("neutral_summary"):
            lines.extend(["### Neutral", "", str(risk["neutral_summary"]), ""])
        if risk.get("judge_decision"):
            lines.extend(["### Portfolio decision", "", str(risk["judge_decision"]), ""])

    analysts = payload.get("analyst_reports") or {}
    if any(analysts.values()):
        lines.extend(["## Analyst reports", ""])
        for key, label in (
            ("market", "Market"),
            ("sentiment", "Sentiment"),
            ("news", "News"),
            ("fundamentals", "Fundamentals"),
        ):
            body = analysts.get(key)
            if body:
                lines.extend([f"### {label}", "", str(body), ""])

    final = payload.get("final_trade_decision")
    if final:
        lines.extend(["## Final trade decision", "", str(final), ""])

    return "\n".join(line for line in lines if line is not None).strip() + "\n"
