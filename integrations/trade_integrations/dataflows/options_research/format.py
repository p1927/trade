"""Render OptionsResearchDoc as markdown for agents and CLI."""

from __future__ import annotations

import json

from trade_integrations.dataflows.company_research.models import StageResult

from .models import OptionsResearchDoc


def _stage_table(stages: list[StageResult]) -> str:
    if not stages:
        return "_No stages run._\n"
    lines = ["| Stage | Vendor | Status |", "|-------|--------|--------|"]
    for stage in stages:
        lines.append(f"| {stage.stage} | {stage.vendor} | {stage.status} |")
    return "\n".join(lines) + "\n"


def _events_table(events: list[dict]) -> str:
    if not events:
        return "_No events in window._\n"
    lines = [
        "| Date | Type | Impact (price / vol) | Detail |",
        "|------|------|----------------------|--------|",
    ]
    for event in events[:15]:
        lines.append(
            f"| {event.get('date') or '—'} | {event.get('type') or '—'} | "
            f"{event.get('impact_on_price', '—')} / {event.get('impact_on_vol', '—')} | "
            f"{(event.get('description') or '')[:60]} |"
        )
    return "\n".join(lines) + "\n"


def _strategies_table(strategies: list[dict]) -> str:
    if not strategies:
        return "_No ranked strategies — check chain stage health._\n"
    lines = [
        "| Rank | Strategy | Tier | Score | PoP | Max P | Max L |",
        "|------|----------|------|-------|-----|-------|-------|",
    ]
    for i, s in enumerate(strategies[:8], 1):
        lines.append(
            f"| {i} | {s.get('name', '—')} | {s.get('tier', '—')} | "
            f"{s.get('score', '—')} | {s.get('pop', '—')} | "
            f"{s.get('max_profit', '—')} | {s.get('max_loss', '—')} |"
        )
    return "\n".join(lines) + "\n"


def format_options_report(doc: OptionsResearchDoc) -> str:
    """Agent-facing markdown summary of the options trade plan."""
    pred = doc.prediction or {}
    rec = doc.recommended or {}
    parts = [
        f"# Options Trade Plan — {doc.underlying}",
        "",
        f"**As of:** {doc.as_of.isoformat()}  ",
        f"**Instrument:** {doc.instrument_type} ({doc.market})  ",
        f"**Expiry:** {doc.expiry or '—'}  ",
        f"**Spot:** {doc.spot or '—'}  ",
        "",
        "## Prediction",
        "",
        f"- **View:** {pred.get('view', '—')}",
        f"- **IV regime:** {pred.get('iv_regime', '—')}",
        f"- **Expected move %:** {pred.get('expected_move_pct', '—')}",
        f"- **Confidence (top score):** {pred.get('confidence', '—')}",
        "",
        "## Events",
        "",
        _events_table(doc.events),
        "## Scenarios",
        "",
    ]
    if doc.scenarios:
        for sc in doc.scenarios:
            parts.append(
                f"- **{sc.get('name')}** ({sc.get('probability', '—')}): "
                f"{sc.get('trigger')} → hint: `{sc.get('strategy_hint')}`"
            )
    else:
        parts.append("_No scenarios generated._")
    parts.extend(
        [
            "",
            "## Ranked strategies",
            "",
            _strategies_table(doc.ranked_strategies),
            "## Recommended",
            "",
        ]
    )
    if rec:
        parts.append(f"**{rec.get('name')}** (tier: {rec.get('tier')}, score: {rec.get('score')})")
        parts.append(f"\n{rec.get('rationale', '')}\n")
        parts.append("**Legs:**")
        for leg in rec.get("legs") or []:
            parts.append(
                f"- {leg.get('side')} {leg.get('quantity')}x {leg.get('symbol')} "
                f"@ {leg.get('price')} (strike {leg.get('strike')} {leg.get('option_type')})"
            )
        if doc.payoff:
            parts.append(
                f"\n**Payoff:** max profit {doc.payoff.get('max_profit')}, "
                f"max loss {doc.payoff.get('max_loss')}, "
                f"breakevens {doc.payoff.get('breakevens')}"
            )
        if doc.charges:
            total = (doc.charges.get("total") or {}).get("total_charges")
            parts.append(f"\n**Charges (est.):** ₹{total}")
        if doc.meta.get("strategy_builder_url"):
            parts.append(f"\n**Strategy Builder:** {doc.meta['strategy_builder_url']}")
        if doc.implementation_steps:
            parts.append("\n## Implementation steps")
            for step in doc.implementation_steps:
                parts.append(
                    f"{step.get('step')}. **{step.get('action')}** — {step.get('description')}"
                )
    else:
        parts.append("_No recommendation — chain or candidate stage may have failed._")

    parts.extend(["", "## Pipeline health", "", _stage_table(doc.stages)])
    parts.append("\n<details><summary>JSON snapshot (recommended)</summary>\n\n```json\n")
    parts.append(json.dumps({"recommended": rec, "prediction": pred}, indent=2, default=str))
    parts.append("\n```\n</details>\n")
    return "\n".join(parts)
