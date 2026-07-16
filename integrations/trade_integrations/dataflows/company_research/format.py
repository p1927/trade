"""Render CompanyResearchDoc as markdown for agents and CLI."""

from __future__ import annotations

import json

from .models import CompanyResearchDoc, StageResult
from .signals_bridge import format_corp_events_section, format_earnings_signal_section


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


def _peers_table(peers: list[dict]) -> str:
    if not peers:
        return "_No peer list yet — check sector context in stage data._\n"
    lines = [
        "| Symbol | Name | Sector | Source |",
        "|--------|------|--------|--------|",
    ]
    for peer in peers[:12]:
        lines.append(
            f"| {peer.get('symbol') or '—'} | {peer.get('name') or '—'} | "
            f"{peer.get('sector') or '—'} | {peer.get('source') or '—'} |"
        )
    return "\n".join(lines) + "\n"


def _news_excerpt(news: dict) -> str:
    if not news:
        return "_News stage not run._\n"
    md = news.get("markdown") or ""
    if not md:
        blocks = news.get("blocks") or []
        parts = []
        for block in blocks:
            parts.append(f"### {block.get('label', block.get('ticker', 'News'))}")
            for row in (block.get("headlines") or [])[:8]:
                parts.append(f"- {row.get('title', row)}")
        md = "\n".join(parts)
    if not md:
        return "_No headlines in lookback window._\n"
    lines = md.splitlines()
    excerpt = "\n".join(lines[:40])
    if len(lines) > 40:
        excerpt += f"\n\n_+ {len(lines) - 40} more lines omitted._"
    return excerpt + "\n"


def _fundamentals_section(data: dict) -> str:
    if not data:
        return "_Fundamentals not available._\n"
    ratios = data.get("ratios") or {}
    if isinstance(data.get("metrics"), dict) and not ratios:
        lines = [f"- **{k}**: {v}" for k, v in list(data["metrics"].items())[:12]]
        return "\n".join(lines) + "\n" if lines else "_No metrics._\n"
    rows = (data.get("financials") or {}).get("rows") or data.get("quarterly_rows") or []
    if ratios or rows:
        lines = ["| Metric | Value |", "|--------|-------|"]
        for key, value in list(ratios.items())[:12]:
            lines.append(f"| {key} | {value} |")
        if rows:
            lines.append("")
            lines.append("**Quarterly financials (₹ Cr)**")
            lines.append("")
            period_cols = [k for k in rows[0].keys() if k != "metric"]
            lines.append("| Metric | " + " | ".join(period_cols) + " |")
            lines.append("|--------|" + "|".join(["---"] * len(period_cols)) + "|")
            for row in rows[:8]:
                cols = [str(row.get("metric") or "—")]
                cols.extend(str(row.get(k) or "—") for k in period_cols)
                lines.append("| " + " | ".join(cols) + " |")
        if len(lines) > 2:
            return "\n".join(lines) + "\n"
    if data.get("quarterly_results"):
        return f"_Quarterly results: {len(data['quarterly_results'])} rows from nselib._\n"
    return f"```json\n{json.dumps(data, default=str)[:800]}\n```\n"


def _filings_section(data: dict) -> str:
    if not data:
        return "_Filings stage not run._\n"
    rows = data.get("filings") or data.get("announcements") or []
    if not rows:
        return "_No recent filings/announcements._\n"
    if rows and isinstance(rows[0], dict) and rows[0].get("source"):
        lines = ["| Date | Type | Title | Source |", "|------|------|-------|--------|"]
        for row in rows[:12]:
            title = (row.get("title") or row.get("description") or row.get("form") or "—")[:120]
            lines.append(
                f"| {row.get('date') or '—'} | {row.get('type') or '—'} | {title} | "
                f"{row.get('source') or '—'} |"
            )
        return "\n".join(lines) + "\n"
    lines = []
    for row in rows[:8]:
        title = row.get("title") or row.get("description") or row.get("form") or "—"
        lines.append(f"- {row.get('date') or '—'}: {title}")
    return "\n".join(lines) + "\n"


def _sentiment_section(data: dict) -> str:
    if not data:
        return "_Sentiment not run._\n"
    summary = data.get("summary") or {}
    if not summary:
        return "_No sentiment summary._\n"
    return (
        f"- Positive: {summary.get('positive_pct', 0)}%\n"
        f"- Negative: {summary.get('negative_pct', 0)}%\n"
        f"- Neutral: {summary.get('neutral_pct', 0)}%\n"
    )


def _macro_section(data: dict) -> str:
    if not data:
        return "_Macro stage not run._\n"
    parts = []
    vix = data.get("india_vix")
    if vix is not None:
        parts.append(f"- India VIX: **{vix}**")
    nifty = data.get("nifty_level")
    if nifty is not None:
        change = data.get("nifty_change_pct")
        suffix = f" ({change:+.2f}%)" if isinstance(change, (int, float)) else ""
        parts.append(f"- Nifty 50: **{nifty}**{suffix}")
    if data.get("fred_excerpt"):
        parts.append("- FRED macro excerpt attached")
    if data.get("polymarket_excerpt"):
        parts.append("- Polymarket macro topics attached")
    if not parts and data.get("india_vix"):
        parts.append(f"- India VIX snapshot available ({data.get('source', 'nselib')})")
    return "\n".join(parts) + "\n" if parts else "_No macro data._\n"


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
        "## Peers",
        _peers_table(doc.peers),
        "",
        f"## Upcoming Events (next {doc.lookahead_days} days)",
        _events_table(doc.calendar_events),
        "",
        "## Fundamentals",
        _fundamentals_section(doc.fundamentals),
        "",
        "## Filings / Announcements",
        _filings_section(doc.filings),
        "",
        "## Recent News",
        _news_excerpt(doc.news),
        "",
        "## Sentiment",
        _sentiment_section(doc.sentiment),
        "",
        "## Earnings Signal (US)",
        format_earnings_signal_section(doc.earnings_signal),
        "",
        "## Corp-Event Forecast (ED-ALPHA)",
        format_corp_events_section(doc.corp_events),
        "",
        "## Macro Context",
        _macro_section(doc.macro),
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
