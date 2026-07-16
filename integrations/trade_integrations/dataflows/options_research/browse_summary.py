"""Compact chain browse summary for agent chat and hub artifact."""

from __future__ import annotations

from typing import Any


def build_browse_summary(chain_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Summarize live chain for in-chat browse (expiries, ATM, top strikes)."""
    chain = chain_snapshot.get("chain") or []
    spot = chain_snapshot.get("underlying_ltp")
    atm = chain_snapshot.get("atm_strike")
    top_strikes: list[dict[str, Any]] = []

    if chain and atm:
        ordered = sorted(chain, key=lambda r: abs(float(r.get("strike") or 0) - float(atm)))
        for row in ordered[:8]:
            strike = row.get("strike")
            ce = row.get("ce") or {}
            pe = row.get("pe") or {}
            top_strikes.append(
                {
                    "strike": strike,
                    "ce_ltp": ce.get("ltp"),
                    "pe_ltp": pe.get("ltp"),
                    "ce_oi": ce.get("oi"),
                    "pe_oi": pe.get("oi"),
                    "ce_iv": ce.get("iv") or ce.get("implied_volatility"),
                    "pe_iv": pe.get("iv") or pe.get("implied_volatility"),
                }
            )

    return {
        "underlying": chain_snapshot.get("underlying"),
        "spot": spot,
        "atm_strike": atm,
        "expiry": chain_snapshot.get("expiry_date"),
        "expiries": list(chain_snapshot.get("expiries") or [])[:8],
        "pcr": chain_snapshot.get("pcr"),
        "total_call_oi": chain_snapshot.get("total_call_oi"),
        "total_put_oi": chain_snapshot.get("total_put_oi"),
        "chain_rows": len(chain),
        "source": chain_snapshot.get("source"),
        "top_strikes": top_strikes,
    }


def format_browse_markdown(summary: dict[str, Any]) -> str:
    """Agent-facing markdown table for in-chat options browse."""
    if not summary:
        return "_No options chain data available._"
    lines = [
        f"## Options browse — {summary.get('underlying') or '—'}",
        "",
        f"- **Spot:** {summary.get('spot', '—')} | **ATM:** {summary.get('atm_strike', '—')} | "
        f"**PCR:** {summary.get('pcr', '—')} | **Expiry:** {summary.get('expiry', '—')}",
    ]
    expiries = summary.get("expiries") or []
    if expiries:
        lines.append(f"- **Expiries:** {', '.join(str(e) for e in expiries[:6])}")
    top = summary.get("top_strikes") or []
    if top:
        lines.extend(
            [
                "",
                "| Strike | CE LTP | PE LTP | CE OI | PE OI | CE IV | PE IV |",
                "|--------|--------|--------|-------|-------|-------|-------|",
            ]
        )
        for row in top[:8]:
            lines.append(
                f"| {row.get('strike', '—')} | {row.get('ce_ltp', '—')} | {row.get('pe_ltp', '—')} | "
                f"{row.get('ce_oi', '—')} | {row.get('pe_oi', '—')} | "
                f"{row.get('ce_iv', '—')} | {row.get('pe_iv', '—')} |"
            )
    else:
        lines.append("\n_No strikes in browse window._")
    return "\n".join(lines)
