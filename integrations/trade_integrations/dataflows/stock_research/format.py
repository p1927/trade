"""Render StockResearchDoc as markdown."""

from __future__ import annotations

from .models import StockResearchDoc


def format_stock_report(doc: StockResearchDoc) -> str:
    pred = doc.prediction or {}
    rec = doc.recommended or {}
    browse = doc.browse_summary or {}
    parts = [
        f"# Stock Trade Plan — {doc.ticker}",
        "",
        f"**As of:** {doc.as_of.isoformat()}  ",
        f"**Spot:** {doc.spot or browse.get('last_price') or '—'}  ",
        "",
        "## Browse",
        "",
        f"- **Price:** {browse.get('last_price')} | **Change %:** {browse.get('change_pct')}",
        f"- **Sector:** {browse.get('sector')} | **52w:** {browse.get('low_52w')} – {browse.get('high_52w')}",
        "",
        "## Prediction",
        "",
        f"- **View:** {pred.get('view', '—')}",
        f"- **Horizon:** {pred.get('horizon_days', doc.lookahead_days)} days",
        f"- **Confidence:** {pred.get('confidence', '—')}",
        "",
        "## Ranked approaches",
        "",
    ]
    for i, s in enumerate(doc.ranked_strategies[:5], 1):
        parts.append(f"{i}. **{s.get('name')}** ({s.get('tier')}, {s.get('score')}) — {s.get('rationale')}")
    parts.extend(["", "## Recommended", ""])
    if rec:
        parts.append(
            f"**{rec.get('name')}** — {rec.get('action')} {rec.get('quantity')} @ ₹{rec.get('entry')} "
            f"(target ₹{rec.get('target')}, stop ₹{rec.get('stop')})"
        )
        parts.append(f"\n{rec.get('rationale', '')}\n")
    if doc.charges:
        total = doc.charges.get("total") or {}
        parts.append(f"**Entry charges (est.):** ₹{total.get('total_charges', '—')}")
    if doc.meta.get("strategy_builder_url"):
        parts.append(f"\n**Review:** {doc.meta['strategy_builder_url']}")
    return "\n".join(parts)
