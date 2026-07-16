"""Render IndexResearchDoc as markdown."""

from __future__ import annotations

from .models import IndexResearchDoc


def format_index_report(doc: IndexResearchDoc) -> str:
    count = len(doc.constituent_signals or [])
    parts = [
        f"# Index Research — {doc.ticker}",
        "",
        f"**As of:** {doc.as_of.isoformat()}",
        "",
        f"**Constituents analyzed:** {count}",
    ]
    return "\n".join(parts) + "\n"
