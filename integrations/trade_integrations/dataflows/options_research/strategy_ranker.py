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


def _signal_fit(tags: list[str], signals: dict[str, Any]) -> float:
    """Bonus from Finverse beat probability and ED-ALPHA corp-event score."""
    if not signals:
        return 0.0
    bonus = 0.0
    bias = signals.get("earnings_bias")
    beat = signals.get("beat_probability")
    if bias == "bullish" and "directional" in tags:
        bonus += 0.12
    if bias == "bearish" and "directional" in tags:
        bonus += 0.08
    if beat is not None and "event" in tags:
        bonus += 0.08
    corp_score = signals.get("corp_event_score")
    corp_rank = signals.get("corp_event_rank")
    if corp_score is not None and float(corp_score) >= 50:
        if "event" in tags or "long_vol" in tags:
            bonus += 0.1
        if "short_vol" in tags and corp_rank is not None and int(corp_rank) <= 25:
            bonus += 0.05
    if signals.get("corp_event_status") == "no_data" and "event" in tags:
        bonus += 0.03
    return min(0.2, bonus)


def _event_fit(
    tags: list[str],
    events: list[dict[str, Any]],
    iv_regime: str,
    *,
    signals: dict[str, Any] | None = None,
) -> float:
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
    for event in events:
        if event.get("type") == "earnings_signal" and "event" in tags:
            score += 0.1
        if event.get("type") == "corp_event_forecast" and "long_vol" in tags:
            score += 0.08
    score += _signal_fit(tags, signals or {})
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
    prediction_signals: dict[str, Any] | None = None,
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
        metrics = estimate_strategy_metrics(
            legs,
            spot=spot,
            broker_preset=broker_preset,
            expiry=str(chain_snapshot.get("expiry_date") or ""),
            iv=analytics.get("atm_iv") or analytics.get("atm_vol"),
        )
        pop = metrics.get("pop") or 0.5
        max_profit = metrics.get("max_profit")
        max_loss = metrics.get("max_loss")
        event_fit = _event_fit(
            cand.get("tags") or [],
            events,
            iv_regime,
            signals=prediction_signals,
        )
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
        try:
            from trade_integrations.dataflows.options_research.prediction_ledger import (
                calibration_confidence_adjustment,
            )

            score += calibration_confidence_adjustment()
        except Exception:
            pass
        try:
            from trade_integrations.autonomous_agents.outcome_ledger import (
                execution_calibration_adjustment,
                agent_strategy_calibration_adjustment,
            )

            score += agent_strategy_calibration_adjustment(cand.get("name"))
            score += execution_calibration_adjustment(cand.get("name"))
        except Exception:
            pass
        score = max(0.0, min(1.0, score))
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
                "payoff_over_time": metrics.get("payoff_over_time"),
                "charges": metrics.get("charges"),
                "event_fit": round(event_fit, 3),
                "net_debit_credit": metrics.get("net_debit_credit"),
                "net_max_profit": metrics.get("net_max_profit"),
                "net_max_loss": metrics.get("net_max_loss"),
                "pop_source": metrics.get("pop_source"),
                "signal_fit": round(_signal_fit(cand.get("tags") or [], prediction_signals or {}), 3),
            }
        )

    ranked.sort(key=lambda r: r.get("score", 0), reverse=True)
    return ranked


def build_scenarios(events: list[dict[str, Any]], ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map scenario archetypes to distinct ranked strategies."""
    names = [r.get("name") for r in ranked if r.get("name")]
    if not names:
        return [
            {
                "name": "base_case",
                "probability": 1.0,
                "trigger": "No ranked strategies — refresh with live OpenAlgo chain",
                "strategy_hint": "iron_condor",
            }
        ]

    def _pick(predicate) -> str | None:
        for name in names:
            if predicate(name.lower()):
                return name
        return None

    top = names[0]
    bullish = _pick(lambda n: "bull" in n or ("call" in n and "spread" in n)) or top
    bearish = _pick(lambda n: "bear" in n or ("put" in n and "spread" in n)) or names[min(1, len(names) - 1)]
    high_vol = _pick(lambda n: "straddle" in n or "strangle" in n) or top
    range_bound = _pick(lambda n: "condor" in n or "butterfly" in n) or names[-1]

    archetypes: list[tuple[str, float, str, str]] = [
        ("base_case", 0.35, "Agent-ranked default — matches recommended strategy", top),
        ("bullish_breakout", 0.25, "Spot rallies toward upper expected range", bullish),
        ("bearish_breakdown", 0.22, "Spot sells off toward lower expected range", bearish),
        ("high_vol_event", 0.18, "Volatility expands (event / gap risk)", high_vol),
    ]

    meaningful_events = [
        e
        for e in events
        if e.get("type") not in ("india_vix", "macro_watch", "fii_dii_flow")
    ]
    if meaningful_events and range_bound not in (top, bullish, bearish, high_vol):
        archetypes.append(
            (
                f"event_{meaningful_events[0].get('type', 'calendar')}",
                0.15,
                meaningful_events[0].get("description") or meaningful_events[0].get("type", "Event"),
                range_bound,
            )
        )

    seen: set[str] = set()
    scenarios: list[dict[str, Any]] = []
    for name_key, prob, trigger, hint in archetypes:
        if hint in seen or len(scenarios) >= 4:
            continue
        seen.add(hint)
        scenarios.append(
            {
                "name": name_key,
                "probability": prob,
                "trigger": trigger,
                "strategy_hint": hint,
            }
        )

    if not scenarios:
        scenarios.append(
            {
                "name": "base_case",
                "probability": 0.55,
                "trigger": "Default research view",
                "strategy_hint": top,
            }
        )
    return scenarios
