"""Rank strategy candidates — Acelogic-style gates + composite score."""

from __future__ import annotations

from typing import Any

from .payoff_charges import estimate_strategy_metrics


def _tier_label(
    *,
    iv_rank_ok: bool,
    liquidity_ok: bool,
    event_fit: float,
    pop: float,
) -> str:
    if iv_rank_ok and liquidity_ok and event_fit >= 0.5 and pop >= 0.45:
        return "Recommended"
    if (iv_rank_ok or liquidity_ok) and pop >= 0.35:
        return "Consider"
    return "Avoid"


def _event_fit(tags: list[str], events: list[dict[str, Any]], iv_regime: str) -> float:
    if not events:
        return 0.5
    has_event = any(e.get("type") not in ("india_vix", "macro_watch", "fii_dii_flow") for e in events)
    score = 0.5
    if has_event and "event" in tags:
        score += 0.25
    if has_event and "short_vol" in tags:
        score += 0.15
    if iv_regime == "high" and "short_vol" in tags:
        score += 0.2
    if iv_regime == "low" and "long_vol" in tags:
        score += 0.2
    if "range" in tags and iv_regime in ("moderate", "high"):
        score += 0.1
    return min(1.0, score)


def _liquidity_ok(legs: list[dict[str, Any]]) -> bool:
    for leg in legs:
        if not leg.get("symbol"):
            return False
        if float(leg.get("price") or 0) <= 0:
            return False
    return len(legs) > 0


def rank_strategies(
    candidates: list[dict[str, Any]],
    *,
    chain_snapshot: dict[str, Any],
    analytics: dict[str, Any],
    history: dict[str, Any],
    events: list[dict[str, Any]],
    spot: float,
    broker_preset: str = "zerodha",
) -> list[dict[str, Any]]:
    """Score and sort candidates; attach payoff metrics and tier labels."""
    atm_iv = analytics.get("atm_iv") or analytics.get("atm_vol")
    if atm_iv is None and analytics.get("expected_move"):
        atm_iv = 18.0
    rv30 = history.get("rv30_pct")
    iv_rank_ok = False
    if atm_iv and rv30:
        iv_rank_ok = float(atm_iv) / float(rv30) >= 1.15
    elif atm_iv:
        iv_rank_ok = float(atm_iv) >= 16
    iv_regime = str(analytics.get("iv_regime") or "moderate")

    ranked: list[dict[str, Any]] = []
    for cand in candidates:
        legs = cand.get("legs") or []
        if not _liquidity_ok(legs):
            continue
        metrics = estimate_strategy_metrics(legs, spot=spot, broker_preset=broker_preset)
        pop = metrics.get("pop") or 0.5
        max_profit = metrics.get("max_profit")
        max_loss = metrics.get("max_loss")
        event_fit = _event_fit(cand.get("tags") or [], events, iv_regime)
        liquidity_ok = _liquidity_ok(legs)
        rr = 0.5
        if max_profit and max_loss and max_loss < 0:
            rr = min(2.0, abs(max_profit) / abs(max_loss))

        score = (
            0.30 * event_fit
            + 0.25 * (pop if pop <= 1 else pop / 100)
            + 0.20 * (1.0 if iv_rank_ok and "short_vol" in (cand.get("tags") or []) else 0.6)
            + 0.15 * min(1.0, rr)
            + 0.10 * (1.0 if liquidity_ok else 0.0)
        )
        tier = _tier_label(iv_rank_ok=iv_rank_ok, liquidity_ok=liquidity_ok, event_fit=event_fit, pop=pop)

        ranked.append(
            {
                **cand,
                "score": round(score, 3),
                "tier": tier,
                "pop": round(pop, 3) if pop <= 1 else round(pop / 100, 3),
                "max_profit": max_profit,
                "max_loss": max_loss,
                "breakevens": metrics.get("breakevens"),
                "payoff": metrics.get("payoff"),
                "charges": metrics.get("charges"),
                "event_fit": round(event_fit, 3),
            }
        )

    ranked.sort(key=lambda r: r.get("score", 0), reverse=True)
    return ranked


def build_scenarios(events: list[dict[str, Any]], ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map events to scenario hints using top strategy tags."""
    top = ranked[0] if ranked else {}
    top_name = top.get("name", "iron_condor")
    scenarios = []
    if not events:
        scenarios.append(
            {
                "name": "base_case",
                "probability": 0.55,
                "trigger": "No major event in window",
                "strategy_hint": top_name,
            }
        )
        return scenarios

    for i, event in enumerate(events[:5]):
        vol_impact = event.get("impact_on_vol", "moderate")
        hint = top_name
        if vol_impact in ("elevated", "high"):
            hint = "long_straddle" if any(r.get("name") == "long_straddle" for r in ranked) else top_name
        elif vol_impact == "low":
            hint = "iron_condor" if any(r.get("name") == "iron_condor" for r in ranked) else top_name
        scenarios.append(
            {
                "name": f"scenario_{i + 1}_{event.get('type', 'event')}",
                "probability": round(0.35 / max(1, len(events[:5])), 2),
                "trigger": event.get("description") or event.get("type"),
                "strategy_hint": hint,
            }
        )
    return scenarios
