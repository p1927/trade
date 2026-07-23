"""Scenario-weighted strategy scoring for pre-turn advisory."""

from __future__ import annotations

from typing import Any

from trade_integrations.autonomous_agents.defaults import DEFAULT_MIN_STRATEGY_SCORE
from trade_integrations.context.hub import load_options_research_json
from trade_integrations.dataflows.options_research.payoff_charges import compute_payoff, estimate_strategy_metrics


def score_ranked_strategies(
    ticker: str,
    *,
    tried: list[str] | None = None,
    spot: float | None = None,
) -> list[dict[str, Any]]:
    """Return ranked strategies with scenario-weighted net EV (INR)."""
    doc = load_options_research_json(ticker)
    if doc is None:
        return []

    min_score = DEFAULT_MIN_STRATEGY_SCORE
    tried_set = {n.strip().lower() for n in (tried or []) if n}
    live_spot = spot
    if live_spot is None:
        live_spot = float(getattr(doc, "spot", None) or (doc.spot if hasattr(doc, "spot") else 0) or 0)
    if live_spot <= 0:
        return []

    scenarios = getattr(doc, "scenarios", None) or []
    scenario_weights: list[tuple[float, float]] = []
    for row in scenarios:
        if isinstance(row, dict):
            prob = float(row.get("probability") or row.get("weight") or 0)
            target = float(row.get("spot") or row.get("target_spot") or live_spot)
        else:
            prob = float(getattr(row, "probability", 0) or getattr(row, "weight", 0) or 0)
            target = float(getattr(row, "spot", None) or getattr(row, "target_spot", None) or live_spot)
        if prob > 0:
            scenario_weights.append((prob, target))
    if not scenario_weights:
        scenario_weights = [(1.0, live_spot)]

    total_prob = sum(p for p, _ in scenario_weights) or 1.0
    scored: list[dict[str, Any]] = []

    for row in doc.ranked_strategies or []:
        name = str(getattr(row, "name", None) or (row.get("name") if isinstance(row, dict) else "") or "")
        if not name or name.strip().lower() in tried_set:
            continue
        score = getattr(row, "score", None)
        if score is None and isinstance(row, dict):
            score = row.get("score")
        try:
            base_score = float(score or 0.0)
        except (TypeError, ValueError):
            base_score = 0.0
        if base_score < min_score:
            continue

        legs = getattr(row, "legs", None) or (row.get("legs") if isinstance(row, dict) else None) or []
        if not legs and hasattr(doc, "recommended"):
            rec = doc.recommended
            if rec and getattr(rec, "name", None) == name:
                legs = getattr(rec, "legs", None) or []

        ev_inr = 0.0
        max_loss_inr: float | None = None
        if legs:
            metrics = estimate_strategy_metrics(legs, spot=live_spot)
            max_loss_inr = metrics.get("max_loss_inr")
            for prob, target_spot in scenario_weights:
                weight = prob / total_prob
                payoff = compute_payoff(legs, target_spot, steps=40)
                samples = payoff.get("samples") or []
                if samples:
                    mid = samples[len(samples) // 2]
                    ev_inr += weight * float(mid.get("pnl") or 0)

        scored.append(
            {
                "name": name,
                "base_score": base_score,
                "ev_inr": round(ev_inr, 2),
                "max_loss_inr": max_loss_inr,
                "confidence_adjusted_score": round(base_score, 4),
            }
        )

    scored.sort(key=lambda r: (r["ev_inr"], r["base_score"]), reverse=True)
    return scored[:5]


def format_scorer_for_prompt(scored: list[dict[str, Any]]) -> str:
    if not scored:
        return ""
    lines = ["## Deterministic strategy scores (advisory)", ""]
    lines.append("| Strategy | EV (INR) | Base score | Max loss |")
    lines.append("|----------|----------|------------|----------|")
    for row in scored:
        max_loss = row.get("max_loss_inr")
        max_loss_s = f"₹{max_loss:,.0f}" if max_loss is not None else "—"
        lines.append(
            f"| {row['name']} | {row['ev_inr']:,.0f} | {row['base_score']:.2f} | {max_loss_s} |"
        )
    lines.append("")
    lines.append("Cite a scorer row if you deviate from the top-ranked candidate.")
    return "\n".join(lines)
