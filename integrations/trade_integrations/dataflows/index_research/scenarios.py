"""Event-driven index scenario builder."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from trade_integrations.dataflows.index_research.attribution import (
    _is_earnings_event,
    _parse_event_date,
)
from trade_integrations.dataflows.index_research.models import ConstituentSignal


def _today() -> date:
    return date.today()


def _count_earnings_within_horizon(
    signals: list[ConstituentSignal],
    *,
    horizon_days: int,
) -> int:
    as_of = _today()
    deadline = as_of + timedelta(days=horizon_days)
    count = 0
    for signal in signals:
        for event in signal.events:
            if not _is_earnings_event(event):
                continue
            event_date = _parse_event_date(event.get("date"))
            if event_date is None or as_of <= event_date <= deadline:
                count += 1
    return count


def _index_range(spot: float, low_pct: float, high_pct: float) -> list[float]:
    return [
        round(spot * (1 + low_pct / 100), 2),
        round(spot * (1 + high_pct / 100), 2),
    ]


_EVENT_LABELS: dict[str, str] = {
    "earnings_cluster": "Earnings cluster",
    "rbi_policy": "RBI policy",
    "union_budget": "Union budget",
    "monthly_expiry": "Monthly expiry",
    "macro_drift": "Macro drift",
}

_OUTCOME_LABELS: dict[str, str] = {
    "positive_surprises": "Positive surprises",
    "negative_surprises": "Negative surprises",
    "dovish_hold": "Dovish hold",
    "hawkish_surprise": "Hawkish surprise",
    "market_friendly": "Market-friendly",
    "fiscal_shock": "Fiscal shock",
    "range_bound": "Range-bound",
    "breakout": "Breakout",
    "neutral": "Neutral drift",
}

_SCENARIO_DESCRIPTIONS: dict[tuple[str, str], str] = {
    ("earnings_cluster", "positive_surprises"): (
        "Multiple Nifty heavyweights report within the horizon; more beats than misses "
        "typically lift the index toward the upper part of this band."
    ),
    ("earnings_cluster", "negative_surprises"): (
        "Clustered results with guidance cuts or misses can drag the index toward the lower band."
    ),
    ("rbi_policy", "dovish_hold"): (
        "Status-quo or dovish RBI messaging tends to support risk appetite and a modest upside bias."
    ),
    ("rbi_policy", "hawkish_surprise"): (
        "A hawkish surprise (higher rates or tighter guidance) often pressures financials and the index."
    ),
    ("union_budget", "market_friendly"): (
        "A growth-oriented budget with stable fiscal math can extend a rally into the upper range."
    ),
    ("union_budget", "fiscal_shock"): (
        "Unexpected tax hikes or fiscal slippage can trigger a sharp risk-off move toward the lower band."
    ),
    ("monthly_expiry", "range_bound"): (
        "Max-pain / pinning dynamics around monthly F&O expiry often keep Nifty in a tight range."
    ),
    ("monthly_expiry", "breakout"): (
        "Heavy rollover or short covering into expiry can produce a wider swing than usual."
    ),
    ("macro_drift", "neutral"): (
        "No dominant event — index drifts with global cues, flows, and sentiment without a single catalyst."
    ),
}


def _scenario_label(event: str, outcome: str) -> str:
    event_label = _EVENT_LABELS.get(event, event.replace("_", " ").title())
    outcome_label = _OUTCOME_LABELS.get(outcome, outcome.replace("_", " ").title())
    return f"{event_label} · {outcome_label}"


def _enrich_scenario(scenario: dict[str, Any], *, spot: float) -> dict[str, Any]:
    event = str(scenario.get("event") or "")
    outcome = str(scenario.get("outcome") or "")
    enriched = dict(scenario)
    enriched["label"] = _scenario_label(event, outcome)
    enriched["description"] = _SCENARIO_DESCRIPTIONS.get(
        (event, outcome),
        f"If {event.replace('_', ' ')} resolves as {outcome.replace('_', ' ')}, "
        f"Nifty is modeled in the shown index range.",
    )
    raw_range = scenario.get("index_range")
    if isinstance(raw_range, (list, tuple)) and len(raw_range) >= 2 and spot > 0:
        try:
            low = float(raw_range[0])
            high = float(raw_range[1])
            midpoint = (low + high) / 2.0
            enriched["midpoint_return_pct"] = round(((midpoint / spot) - 1.0) * 100.0, 2)
        except (TypeError, ValueError):
            pass
    return enriched


def _finalize_scenarios(scenarios: list[dict], *, spot: float) -> list[dict]:
    enriched = [_enrich_scenario(scenario, spot=spot) for scenario in scenarios[:6]]
    total_prob = sum(float(item.get("probability") or 0.0) for item in enriched)
    if total_prob > 0 and abs(total_prob - 1.0) > 0.01:
        for item in enriched:
            raw = float(item.get("probability") or 0.0)
            item["probability_raw"] = raw
            item["probability"] = round(raw / total_prob, 4)
    enriched.sort(key=lambda item: float(item.get("probability") or 0.0), reverse=True)
    return enriched


def _has_upcoming_rbi(macro_factors: dict, *, horizon_days: int) -> bool:
    events = macro_factors.get("rbi_events") or []
    if not isinstance(events, list):
        events = []
    deadline = _today() + timedelta(days=horizon_days)
    for event in events:
        if not isinstance(event, dict):
            continue
        event_date = event.get("date")
        if event_date is None:
            return True
        if isinstance(event_date, datetime):
            parsed = event_date.date()
        else:
            try:
                parsed = date.fromisoformat(str(event_date)[:10])
            except ValueError:
                continue
        if _today() <= parsed <= deadline:
            return True
    return macro_factors.get("repo_rate") is not None


def build_index_scenarios(
    signals: list[ConstituentSignal],
    macro_factors: dict,
    *,
    spot: float,
    horizon_days: int,
) -> list[dict]:
    """Build 3–6 event scenarios with index ranges anchored to spot."""
    scale = horizon_days / 14.0
    scenarios: list[dict] = []

    earnings_count = _count_earnings_within_horizon(signals, horizon_days=horizon_days)
    if earnings_count >= 2:
        scenarios.append(
            {
                "event": "earnings_cluster",
                "outcome": "positive_surprises",
                "index_range": _index_range(spot, -0.5 * scale, 2.0 * scale),
                "probability": 0.35,
            }
        )
        scenarios.append(
            {
                "event": "earnings_cluster",
                "outcome": "negative_surprises",
                "index_range": _index_range(spot, -2.5 * scale, 0.5 * scale),
                "probability": 0.25,
            }
        )

    if _has_upcoming_rbi(macro_factors, horizon_days=horizon_days):
        scenarios.append(
            {
                "event": "rbi_policy",
                "outcome": "dovish_hold",
                "index_range": _index_range(spot, 0.0 * scale, 1.5 * scale),
                "probability": 0.4,
            }
        )
        scenarios.append(
            {
                "event": "rbi_policy",
                "outcome": "hawkish_surprise",
                "index_range": _index_range(spot, -2.0 * scale, 0.5 * scale),
                "probability": 0.2,
            }
        )

    if float(macro_factors.get("is_budget_week") or 0.0) >= 1.0:
        scenarios.append(
            {
                "event": "union_budget",
                "outcome": "market_friendly",
                "index_range": _index_range(spot, 0.0 * scale, 2.5 * scale),
                "probability": 0.3,
            }
        )
        scenarios.append(
            {
                "event": "union_budget",
                "outcome": "fiscal_shock",
                "index_range": _index_range(spot, -3.0 * scale, 0.5 * scale),
                "probability": 0.2,
            }
        )

    scenarios.append(
        {
            "event": "monthly_expiry",
            "outcome": "range_bound",
            "index_range": _index_range(spot, -1.0 * scale, 1.0 * scale),
            "probability": 0.45,
        }
    )
    scenarios.append(
        {
            "event": "monthly_expiry",
            "outcome": "breakout",
            "index_range": _index_range(spot, -2.5 * scale, 2.5 * scale),
            "probability": 0.25,
        }
    )

    if len(scenarios) < 3:
        scenarios.append(
            {
                "event": "macro_drift",
                "outcome": "neutral",
                "index_range": _index_range(spot, -1.5 * scale, 1.5 * scale),
                "probability": 0.5,
            }
        )

    return _finalize_scenarios(scenarios, spot=spot)


def scenario_weighted_return_pct(
    scenarios: list[dict],
    *,
    spot: float,
) -> float | None:
    """Probability-weighted midpoint return from event scenarios (spot-anchored)."""
    if not scenarios or spot <= 0:
        return None
    total_weight = 0.0
    weighted = 0.0
    for scenario in scenarios:
        raw_range = scenario.get("index_range")
        if not isinstance(raw_range, (list, tuple)) or len(raw_range) < 2:
            continue
        try:
            low = float(raw_range[0])
            high = float(raw_range[1])
            prob = float(scenario.get("probability") or 0.0)
        except (TypeError, ValueError):
            continue
        if prob <= 0:
            continue
        midpoint = (low + high) / 2.0
        weighted += ((midpoint / spot) - 1.0) * 100.0 * prob
        total_weight += prob
    if total_weight <= 0:
        return None
    return round(weighted / total_weight, 4)


def reconcile_prediction_with_scenarios(
    prediction: dict[str, Any],
    scenarios: list[dict],
    *,
    spot: float,
    mae_pct: float = 1.5,
    divergence_threshold_pct: float = 1.5,
) -> dict[str, Any]:
    """Blend macro-model headline toward scenario consensus when they diverge sharply."""
    anchor = scenario_weighted_return_pct(scenarios, spot=spot)
    if anchor is None or not prediction:
        return prediction

    bottom_up = float(prediction.get("bottom_up_return_pct") or 0.0)
    raw = float(prediction.get("expected_return_pct") or 0.0)
    if abs(raw - anchor) <= divergence_threshold_pct:
        return prediction

    from trade_integrations.dataflows.index_research.predictor import cap_macro_delta

    blended = round(0.25 * raw + 0.75 * anchor, 4)
    macro_delta = cap_macro_delta(blended - bottom_up)
    expected = round(bottom_up + macro_delta, 4)
    range_block = dict(prediction.get("range") or {})

    updated = dict(prediction)
    updated["raw_expected_return_pct"] = raw
    updated["raw_macro_delta_pct"] = float(prediction.get("macro_delta_pct") or 0.0)
    updated["expected_return_pct"] = expected
    updated["macro_delta_pct"] = macro_delta
    updated["scenario_anchor_return_pct"] = anchor
    updated["reconciled_with_scenarios"] = True
    updated["reconciliation_blend"] = {"model_weight": 0.25, "scenario_weight": 0.75}
    updated["range"] = {
        **range_block,
        "low": spot * (1 + expected / 100 - mae_pct / 100),
        "high": spot * (1 + expected / 100 + mae_pct / 100),
    }
    return updated
