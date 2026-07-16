"""Factor trust snippets from walk-forward diagnostics (QuantMuse IC-style pedagogy)."""

from __future__ import annotations

from typing import Any


def _corr_strength(abs_corr: float) -> str:
    if abs_corr >= 0.15:
        return "strong"
    if abs_corr >= 0.08:
        return "moderate"
    if abs_corr >= 0.05:
        return "weak"
    return "negligible"


def load_factor_trust_map(ticker: str = "NIFTY") -> dict[str, dict[str, Any]]:
    """Build per-factor trust metadata from equation_diagnostics_latest.json."""
    try:
        from trade_integrations.dataflows.index_research.equation_diagnostics import (
            load_diagnostics_report,
        )
    except ImportError:
        return {}

    report = load_diagnostics_report(ticker)
    if not report:
        return {}

    baseline = report.get("baseline_direction_hit_rate")
    corr_rows = report.get("factor_correlations") or []
    corr_by_factor: dict[str, dict[str, Any]] = {}
    for row in corr_rows:
        factor = str(row.get("factor") or row.get("term") or "")
        if not factor:
            continue
        corr_by_factor[factor] = row

    ablation_by_factor: dict[str, float] = {}
    for block in report.get("block_ablation") or []:
        delta = block.get("delta_pp")
        if delta is None:
            continue
        for factor in block.get("factors") or []:
            key = str(factor)
            prev = ablation_by_factor.get(key)
            if prev is None or abs(float(delta)) > abs(prev):
                ablation_by_factor[key] = float(delta)

    trust: dict[str, dict[str, Any]] = {}
    for factor, row in corr_by_factor.items():
        corr = row.get("correlation") or row.get("corr")
        try:
            corr_f = float(corr) if corr is not None else None
        except (TypeError, ValueError):
            corr_f = None

        delta_pp = ablation_by_factor.get(factor)
        snippet_parts: list[str] = []
        if baseline is not None:
            snippet_parts.append(f"Model baseline direction hit {float(baseline) * 100:.1f}%")
        if corr_f is not None:
            direction = "positive" if corr_f > 0 else "negative"
            snippet_parts.append(
                f"{_corr_strength(abs(corr_f))} {direction} correlation ({corr_f:+.3f}) to 14d forward return"
            )
        if delta_pp is not None:
            if delta_pp > 0.5:
                snippet_parts.append(f"removing factor hurts OOS by {delta_pp:+.1f}pp — keep in model")
            elif delta_pp < -0.5:
                snippet_parts.append(f"removing factor helps OOS by {abs(delta_pp):.1f}pp — use cautiously")

        trust[factor] = {
            "correlation": corr_f,
            "ablation_delta_pp": delta_pp,
            "trust_snippet": "; ".join(snippet_parts) if snippet_parts else None,
        }
    return trust


def enrich_factor_notes_with_trust(
    factors: dict[str, Any],
    *,
    ticker: str = "NIFTY",
    limit: int = 8,
) -> dict[str, str]:
    """Merge playbook summaries with diagnostics trust snippets."""
    from trade_integrations.knowledge.interpret import factor_notes_for_snapshot

    notes = factor_notes_for_snapshot(factors, limit=limit)
    trust_map = load_factor_trust_map(ticker)
    enriched: dict[str, str] = {}
    for key, summary in notes.items():
        trust = trust_map.get(key, {})
        snippet = trust.get("trust_snippet")
        if snippet:
            enriched[key] = f"{summary} [{snippet}]"
        else:
            enriched[key] = summary
    return enriched
