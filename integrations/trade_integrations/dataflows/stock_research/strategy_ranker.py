"""Rank stock trade approaches from company research signals."""

from __future__ import annotations

from typing import Any


def _score_candidate(name: str, signals: dict[str, Any]) -> tuple[float, str, str]:
    sentiment = float(signals.get("sentiment_score") or 0)
    earnings = signals.get("earnings_signal") or {}
    beat = float(earnings.get("beat_probability") or 0.5)
    pe = signals.get("pe_ratio")
    change = float(signals.get("change_pct") or 0)

    if name == "buy_dip":
        score = 0.55 + (0.15 if sentiment > 0.2 else 0) + (0.1 if change < -1 else 0)
        tier = "Recommended" if score >= 0.7 else "Consider"
        rationale = "Pullback with supportive sentiment — accumulate on weakness."
    elif name == "momentum_breakout":
        score = 0.5 + (0.2 if change > 1.5 else 0) + (0.1 if sentiment > 0.3 else 0)
        tier = "Recommended" if score >= 0.72 else "Consider"
        rationale = "Positive momentum and sentiment — ride trend with defined stop."
    elif name == "event_play":
        score = 0.52 + (0.2 if beat > 0.55 else 0) + (0.1 if signals.get("has_near_event") else 0)
        tier = "Consider"
        rationale = "Upcoming catalyst — size small until event resolves."
    elif name == "hold_cash":
        score = 0.6 if sentiment < -0.2 or change < -3 else 0.45
        tier = "Avoid" if score >= 0.55 else "Consider"
        rationale = "Weak tape or negative sentiment — wait for better entry."
    else:
        score = 0.5
        tier = "Consider"
        rationale = "Neutral stance."

    if pe and float(pe) > 60:
        score -= 0.08
        rationale += " Valuation stretched (high P/E)."
    return round(min(max(score, 0.2), 0.95), 3), tier, rationale


def rank_stock_strategies(
    company_doc: dict[str, Any],
    *,
    spot: float,
) -> list[dict[str, Any]]:
    """Return ranked stock actions with scores."""
    identity = company_doc.get("identity") or {}
    sentiment = company_doc.get("sentiment") or {}
    earnings = company_doc.get("earnings_signal") or {}
    events = company_doc.get("calendar_events") or []
    signals = {
        "sentiment_score": sentiment.get("score") or sentiment.get("compound"),
        "earnings_signal": earnings,
        "pe_ratio": identity.get("pe_ratio") or identity.get("trailing_pe"),
        "change_pct": identity.get("change_pct"),
        "has_near_event": len(events) > 0,
    }
    names = ["buy_dip", "momentum_breakout", "event_play", "hold_cash"]
    ranked: list[dict[str, Any]] = []
    for name in names:
        score, tier, rationale = _score_candidate(name, signals)
        action = "BUY" if name != "hold_cash" else "HOLD"
        qty = 1
        target = round(spot * 1.05, 2) if action == "BUY" else None
        stop = round(spot * 0.97, 2) if action == "BUY" else None
        ranked.append(
            {
                "name": name,
                "action": action,
                "score": score,
                "tier": tier,
                "rationale": rationale,
                "entry": spot,
                "target": target,
                "stop": stop,
                "quantity": qty,
                "product": "CNC",
            }
        )
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def build_stock_scenarios(events: list[dict[str, Any]], ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map events to suggested stock actions."""
    top = ranked[0]["name"] if ranked else "hold_cash"
    scenarios = [
        {
            "name": "base_case",
            "probability": "medium",
            "trigger": "No major catalyst",
            "strategy_hint": top,
        }
    ]
    for ev in events[:5]:
        scenarios.append(
            {
                "name": ev.get("type", "event"),
                "probability": "medium",
                "trigger": ev.get("description") or ev.get("title") or str(ev.get("date")),
                "strategy_hint": "event_play" if "earn" in str(ev.get("type", "")).lower() else top,
            }
        )
    return scenarios
