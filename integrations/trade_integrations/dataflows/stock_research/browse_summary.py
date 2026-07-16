"""Compact equity browse summary for agent chat."""

from __future__ import annotations

from typing import Any


def build_stock_browse_summary(
    *,
    ticker: str,
    identity: dict[str, Any],
    quote: dict[str, Any] | None = None,
    peers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize live stock context for in-chat browse."""
    q = quote or {}
    return {
        "ticker": ticker,
        "name": identity.get("name") or identity.get("company_name"),
        "sector": identity.get("sector"),
        "industry": identity.get("industry"),
        "last_price": q.get("ltp") or identity.get("last_price"),
        "change_pct": q.get("change_pct") or identity.get("change_pct"),
        "volume": q.get("volume"),
        "high_52w": identity.get("high_52w") or q.get("high_52w"),
        "low_52w": identity.get("low_52w") or q.get("low_52w"),
        "market_cap": identity.get("market_cap"),
        "pe_ratio": identity.get("pe_ratio") or identity.get("trailing_pe"),
        "top_peers": (peers or [])[:5],
        "source": q.get("source") or "hub",
    }


def format_stock_browse_markdown(summary: dict[str, Any]) -> str:
    """Agent-facing markdown for equity browse."""
    if not summary:
        return "_No stock data available._"
    lines = [
        f"## Stock browse — {summary.get('ticker', '—')}",
        "",
        f"- **Price:** {summary.get('last_price', '—')} | **Change %:** {summary.get('change_pct', '—')}",
        f"- **Sector:** {summary.get('sector', '—')} | **Industry:** {summary.get('industry', '—')}",
        f"- **52w range:** {summary.get('low_52w', '—')} – {summary.get('high_52w', '—')}",
        f"- **Volume:** {summary.get('volume', '—')} | **P/E:** {summary.get('pe_ratio', '—')}",
    ]
    peers = summary.get("top_peers") or []
    if peers:
        lines.append("\n| Peer | Price |")
        lines.append("|------|-------|")
        for p in peers[:5]:
            lines.append(f"| {p.get('symbol', p.get('ticker', '—'))} | {p.get('last_price', '—')} |")
    return "\n".join(lines)
