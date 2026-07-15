"""Render CompanyResearchDoc as markdown for agents and CLI."""

from __future__ import annotations

from .models import CompanyResearchDoc, StageResult


def _stage_table(stages: list[StageResult]) -> str:
    if not stages:
        return "_No stages run._\n"
    lines = ["| Stage | Vendor | Status |", "|-------|--------|--------|"]
    for stage in stages:
        lines.append(f"| {stage.stage} | {stage.vendor} | {stage.status} |")
    return "\n".join(lines) + "\n"


def _events_table(events: list[dict]) -> str:
    if not events:
        return "_No upcoming events in the lookahead window._\n"
    lines = [
        "| Date | Type | Detail | Source |",
        "|------|------|--------|--------|",
    ]
    for event in events[:20]:
        detail = event.get("description") or event.get("purpose") or event.get("type") or "—"
        lines.append(
            f"| {event.get('date') or '—'} | {event.get('type') or '—'} | "
            f"{detail} | {event.get('source') or '—'} |"
        )
    if len(events) > 20:
        lines.append(f"\n_+ {len(events) - 20} more events omitted._")
    return "\n".join(lines) + "\n"


def _source_health_table(stages: list[StageResult]) -> str:
    rows: list[str] = [
        "| Source | Stage | Status | Error | Fix |",
        "|--------|-------|--------|-------|-----|",
    ]
    for stage in stages:
        attempts = (stage.data or {}).get("source_attempts") or []
        if not attempts:
            rows.append(
                f"| {stage.vendor} | {stage.stage} | {stage.status} | — | — |"
            )
            continue
        for attempt in attempts:
            rows.append(
                f"| {attempt.get('name', '?')} | {stage.stage} | "
                f"{attempt.get('status', '?')} | "
                f"{attempt.get('error') or '—'} | "
                f"{attempt.get('remediation') or '—'} |"
            )
    return "\n".join(rows) + "\n"


def format_research_report(doc: CompanyResearchDoc) -> str:
    """Format a research dossier as markdown."""
    identity = doc.identity
    sections = [
        f"# Company Research: {doc.ticker} ({doc.market})",
        f"_As of {doc.as_of.isoformat()} · lookahead {doc.lookahead_days} days_",
        "",
        "## Company Overview",
        f"- Name: **{identity.get('name', doc.ticker)}**",
        f"- Sector: {identity.get('sector') or '—'}",
        f"- Industry: {identity.get('industry') or '—'}",
        f"- Exchange: {identity.get('exchange') or identity.get('openalgo_exchange') or '—'}",
        f"- Last price: {identity.get('last_price') or '—'} {identity.get('currency') or ''}".rstrip(),
        "",
        f"## Upcoming Events (next {doc.lookahead_days} days)",
        _events_table(doc.calendar_events),
        "",
        "## Market Routing",
        f"- Market: **{doc.market}**",
        f"- YFinance symbol: `{identity.get('yfinance_symbol', '')}`",
        f"- OpenAlgo: `{identity.get('openalgo_symbol', '')}` @ `{identity.get('openalgo_exchange', '')}`",
        "",
        "## Pipeline Stages",
        _stage_table(doc.stages),
        "",
        "## Data Source Health",
        "_Every backend is attempted; working sources are merged. Failed sources show remediation hints._",
        "",
        _source_health_table(doc.stages),
    ]
    return "\n".join(sections)
