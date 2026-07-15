"""Generate multi-leg strategy candidates from chain snapshot."""

from __future__ import annotations

from typing import Any

from .market import OptionsInstrument


def _strike_ladder(chain: list[dict[str, Any]]) -> list[float]:
    strikes = sorted({float(r.get("strike")) for r in chain if r.get("strike")})
    return strikes


def _nearest_strikes(strikes: list[float], atm: float, count: int = 5) -> list[float]:
    if not strikes:
        return []
    ordered = sorted(strikes, key=lambda s: abs(s - atm))
    return sorted(ordered[:count])


def _leg(
    *,
    side: str,
    option_type: str,
    strike: float,
    price: float,
    symbol: str,
    lot_size: int,
    lots: int = 1,
) -> dict[str, Any]:
    return {
        "side": side,
        "segment": "OPTION",
        "option_type": option_type,
        "strike": strike,
        "price": price,
        "symbol": symbol,
        "lot_size": lot_size,
        "lots": lots,
        "quantity": lot_size * lots,
    }


def _row_leg(chain: list[dict], strike: float, option_type: str, side: str, lots: int = 1) -> dict | None:
    for row in chain:
        if float(row.get("strike", 0)) != strike:
            continue
        key = "ce" if option_type == "CE" else "pe"
        leg = row.get(key) or {}
        ltp = leg.get("ltp") or 0
        if not ltp:
            return None
        return _leg(
            side=side,
            option_type=option_type,
            strike=strike,
            price=float(ltp),
            symbol=str(leg.get("symbol") or ""),
            lot_size=int(leg.get("lotsize") or leg.get("lot_size") or 1),
            lots=lots,
        )
    return None


def generate_candidates(
    instrument: OptionsInstrument,
    chain_snapshot: dict[str, Any],
    *,
    iv_regime: str = "moderate",
    has_event: bool = False,
) -> list[dict[str, Any]]:
    """Build 4–8 concrete strategy candidates with legs."""
    chain = chain_snapshot.get("chain") or []
    if not chain:
        return []

    atm = float(chain_snapshot.get("atm_strike") or chain[0].get("strike") or 0)
    strikes = _strike_ladder(chain)
    if not strikes or atm <= 0:
        return []

    near = _nearest_strikes(strikes, atm, 8)
    idx = strikes.index(min(near, key=lambda s: abs(s - atm))) if near else len(strikes) // 2
    wing = 2
    low_put = strikes[max(0, idx - wing)]
    high_call = strikes[min(len(strikes) - 1, idx + wing)]
    inner_put = strikes[max(0, idx - 1)]
    inner_call = strikes[min(len(strikes) - 1, idx + 1)]

    candidates: list[dict[str, Any]] = []

    def add(name: str, legs: list[dict], rationale: str, tags: list[str]):
        if len(legs) >= 1 and all(legs):
            candidates.append(
                {
                    "name": name,
                    "legs": legs,
                    "rationale": rationale,
                    "tags": tags,
                }
            )

    # Long straddle — high uncertainty / event
    if has_event or iv_regime in ("low", "moderate"):
        legs = [
            _row_leg(chain, atm, "CE", "BUY"),
            _row_leg(chain, atm, "PE", "BUY"),
        ]
        add(
            "long_straddle",
            [l for l in legs if l],
            "Binary event or breakout — profit from large move either direction",
            ["event", "long_vol"],
        )

    # Short strangle — elevated IV, range expectation
    if iv_regime in ("high", "moderate") and has_event:
        legs = [
            _row_leg(chain, inner_put, "PE", "SELL"),
            _row_leg(chain, inner_call, "CE", "SELL"),
        ]
        add(
            "short_strangle",
            [l for l in legs if l],
            "Sell elevated IV before event; needs range-bound outcome after",
            ["short_vol", "event"],
        )

    # Iron condor — range-bound, defined risk
    ic_legs = [
        _row_leg(chain, low_put, "PE", "BUY"),
        _row_leg(chain, inner_put, "PE", "SELL"),
        _row_leg(chain, inner_call, "CE", "SELL"),
        _row_leg(chain, high_call, "CE", "BUY"),
    ]
    add(
        "iron_condor",
        [l for l in ic_legs if l],
        "Range-bound view with capped risk — collect premium between wings",
        ["range", "defined_risk"],
    )

    # Bull call spread
    if idx + 1 < len(strikes):
        bcs = [
            _row_leg(chain, atm, "CE", "BUY"),
            _row_leg(chain, strikes[min(len(strikes) - 1, idx + 2)], "CE", "SELL"),
        ]
        add(
            "bull_call_spread",
            [l for l in bcs if l],
            "Moderately bullish — limited cost directional play",
            ["bullish", "debit"],
        )

    # Bear put spread
    bps = [
        _row_leg(chain, atm, "PE", "BUY"),
        _row_leg(chain, strikes[max(0, idx - 2)], "PE", "SELL"),
    ]
    add(
        "bear_put_spread",
        [l for l in bps if l],
        "Moderately bearish — limited cost directional play",
        ["bearish", "debit"],
    )

    return candidates[:8]
