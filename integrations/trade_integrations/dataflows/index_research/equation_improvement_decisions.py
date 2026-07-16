"""Generate equation improvement decision record from diagnostic artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.equation_diagnostics import load_diagnostics_report
from trade_integrations.dataflows.index_research.prediction_counterfactual import load_counterfactual_report
from trade_integrations.dataflows.index_research.backtest_runner import load_backtest_report
from trade_integrations.dataflows.index_research.t0_information_audit import load_t0_audit_report

_DECISIONS_PATH = lambda ticker: (
    get_hub_dir() / ticker.strip().upper() / "index_research" / "equation_improvement_decisions.md"
)

STRUCTURAL_CHANGES: list[dict[str, Any]] = [
    {
        "id": "delta_features",
        "hypothesis": "Flow/oil/VIX acceleration features capture horizon path not visible in levels.",
        "status": "accepted",
    },
    {
        "id": "regime_gates",
        "hypothesis": "Pre-specified high_fear/trend_down gates reduce mean-reversion and contrarian FII errors.",
        "status": "accepted",
    },
    {
        "id": "scenario_shrinkage",
        "hypothesis": "Shrink |raw|>3% macro toward scenario anchor before hard cap reduces cap_artifact misses.",
        "status": "accepted",
    },
    {
        "id": "hybrid_backtest",
        "hypothesis": "Hybrid bottom-up + macro metrics expose live pipeline parity vs macro-only OOS.",
        "status": "accepted",
    },
    {
        "id": "dii_backfill",
        "hypothesis": "DII coverage >90% required before interpreting DII coefficient.",
        "status": "accepted",
    },
    {
        "id": "lower_ridge_alpha",
        "hypothesis": "Lower Ridge α to improve in-sample R².",
        "status": "rejected",
        "rejection_reason": "Anti-overfitting rule: in-sample R² is not optimization target; OOS walk-forward only.",
    },
    {
        "id": "widen_macro_cap",
        "hypothesis": "Raise cap from ±5% to ±8% to fit magnitude on miss dates.",
        "status": "rejected",
        "rejection_reason": "Cap widening fits historical misses without fixing sign logic.",
    },
]


def _format_decisions_md(
    *,
    ticker: str,
    backtest: dict[str, Any] | None,
    counterfactual: dict[str, Any] | None,
    diagnostics: dict[str, Any] | None,
    t0_audit: dict[str, Any] | None,
) -> str:
    metrics = (backtest or {}).get("metrics") or {}
    cf_summary = (counterfactual or {}).get("summary") or {}
    lines = [
        "# Equation improvement decisions",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Ticker: {ticker}",
        "",
        "## OOS baseline",
        "",
        f"- Macro-only direction hit rate: {metrics.get('macro_only_direction_hit_rate') or metrics.get('direction_hit_rate')}",
        f"- Hybrid direction hit rate: {metrics.get('hybrid_direction_hit_rate')} (n={metrics.get('hybrid_eval_count')})",
        f"- MAE: {metrics.get('mae_pct')}%",
        f"- In-sample R² (not target): {metrics.get('in_sample_r2')}",
        "",
        "## Counterfactual evidence",
        "",
        f"- Mapping errors T0: {cf_summary.get('mapping_error_count')}",
        f"- Drift dominant: {cf_summary.get('drift_dominant_count')}",
        f"- Cap artifacts: {cf_summary.get('cap_artifact_count')}",
        "",
        "## T0 information audit",
        "",
        f"- Tag counts: {json.dumps((t0_audit or {}).get('tag_counts') or {})}",
        "",
        "## Structural change log",
        "",
    ]

    ablation = {row["block"]: row for row in (diagnostics or {}).get("block_ablation") or []}
    for change in STRUCTURAL_CHANGES:
        lines.append(f"### {change['id']} — {change['status']}")
        lines.append("")
        lines.append(f"**Hypothesis:** {change['hypothesis']}")
        if change["status"] == "rejected":
            lines.append(f"**Rejected because:** {change.get('rejection_reason')}")
        else:
            if change["id"] == "delta_features":
                delta_row = ablation.get("delta") or {}
                lines.append(
                    f"**Ablation:** without delta block hit rate "
                    f"{delta_row.get('direction_hit_rate_without_block')} "
                    f"(baseline {delta_row.get('baseline_hit_rate')})."
                )
            if change["id"] == "dii_backfill":
                audit = (backtest or {}).get("factor_audit") or []
                dii = next((r for r in audit if r.get("factor") == "dii_net_5d"), {})
                lines.append(f"**Data:** DII coverage {dii.get('coverage_pct')}%")
        lines.append("")

    lines.append("## Logic conflict register")
    lines.append("")
    for row in (diagnostics or {}).get("logic_conflict_register") or []:
        lines.append(f"- **{row.get('conflict')}:** {row.get('logic')}")
    lines.append("")
    return "\n".join(lines)


def generate_equation_improvement_decisions(*, ticker: str = "NIFTY") -> Path:
    backtest = load_backtest_report(ticker)
    counterfactual = load_counterfactual_report(ticker)
    diagnostics = load_diagnostics_report(ticker)
    t0_audit = load_t0_audit_report(ticker)
    content = _format_decisions_md(
        ticker=ticker,
        backtest=backtest,
        counterfactual=counterfactual,
        diagnostics=diagnostics,
        t0_audit=t0_audit,
    )
    path = _DECISIONS_PATH(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def run_and_save_decisions(**kwargs: Any) -> dict[str, Any]:
    ticker = str(kwargs.get("ticker") or "NIFTY")
    path = generate_equation_improvement_decisions(ticker=ticker)
    return {"status": "ok", "path": str(path), "ticker": ticker}
