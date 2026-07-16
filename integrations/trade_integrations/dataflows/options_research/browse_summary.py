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
