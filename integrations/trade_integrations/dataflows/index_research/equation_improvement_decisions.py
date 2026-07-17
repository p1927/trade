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
from trade_integrations.dataflows.index_research.hub_data_audit import load_data_audit_report
from trade_integrations.dataflows.index_research.t0_information_audit import load_t0_audit_report

_ABLATION_ACCEPT_PP = 3.0

_DECISIONS_PATH = lambda ticker: (
    get_hub_dir() / ticker.strip().upper() / "index_research" / "equation_improvement_decisions.md"
)

STRUCTURAL_CHANGES: list[dict[str, Any]] = [
    {
        "id": "delta_features",
        "hypothesis": "Flow/oil/VIX acceleration features capture horizon path not visible in levels.",
        "status": "rejected",
        "rejection_reason": "Walk-forward OOS direction fell 44.4% → 35.3% (−9.1 pp) on 365d eval; two direction flips + lost eval row.",
    },
    {
        "id": "joint_flow_features",
        "hypothesis": "institutional_net_5d + dii_absorption_ratio capture post-2023 DII-dominance regime.",
        "status": "pending_ablation",
        "ablation_block": "joint_flows",
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
    {
        "id": "direction_confidence_calibration",
        "hypothesis": "Calibrate logistic direction confidence to walk-forward OOS (backtest protocol), not in-sample holdout.",
        "status": "accepted",
    },
    {
        "id": "redundancy_prune",
        "hypothesis": "Drop oil_wti, constituent_momentum_7d, sector_breadth_mean_sentiment to reduce collinearity.",
        "status": "accepted",
    },
    {
        "id": "sector_price_factors",
        "hypothesis": "Sector rotation price factors (breadth, rel strength, bank spread) improve 14d direction OOS.",
        "status": "pending_ablation",
        "promotion_key": "sector_promotion",
    },
    {
        "id": "flow_regime_buckets",
        "hypothesis": "Non-linear flow regime offsets (FII contrarian + DAR absorption) improve direction OOS.",
        "status": "rejected",
        "rejection_reason": (
            "Shipped for regime-conditional logic; walk-forward 50.0% vs 52.9% pre-Phase-2 baseline "
            "(−2.9 pp); +3 pp OOS gate not met."
        ),
    },
    {
        "id": "sign_conflict_gate",
        "hypothesis": "Neutralize direction when macro vs scenario anchor disagree on sign (|raw|>3%).",
        "status": "accepted",
        "validation_note": "Trust/honesty gate — forces neutral on anchor conflict; 4 sign-conflict eval days in latest diagnostics.",
    },
    {
        "id": "headline_event_flags",
        "hypothesis": "T0 geopolitical/oil headline flags improve direction when OOS ablation passes +3 pp.",
        "status": "pending_ablation",
        "promotion_key": "event_promotion",
    },
    {
        "id": "hybrid_deferred_tier3",
        "hypothesis": "Hybrid bottom-up promotion until non-backfill archives and hybrid_eval_count > 0.",
        "status": "rejected",
        "rejection_reason": "Tier 3 deferral — RSS backfill noise; 0 hybrid eval rows in latest backtest.",
    },
    {
        "id": "news_event_overlay",
        "hypothesis": "Calibrated per-topic shock overlay from reconciled impact ledger reduces event_gap misses.",
        "status": "pending_ablation",
    },
]


def _resolve_structural_changes(
    diagnostics: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    ablation = {row["block"]: row for row in (diagnostics or {}).get("block_ablation") or []}
    resolved: list[dict[str, Any]] = []
    for change in STRUCTURAL_CHANGES:
        item = dict(change)
        block = item.pop("ablation_block", None)
        if item.get("promotion_key") and item.get("status") == "pending_ablation":
            promo = (diagnostics or {}).get(item["promotion_key"]) or {}
            delta_pp = promo.get("delta_pp")
            item["promotion"] = promo
            reason = promo.get("reason")
            if promo.get("promoted"):
                item["status"] = "accepted"
            elif reason:
                item["status"] = "rejected"
                item["rejection_reason"] = reason
            else:
                item["status"] = "rejected"
                item["rejection_reason"] = (
                    f"Promotion ablation delta {delta_pp} pp < {_ABLATION_ACCEPT_PP} pp gate "
                    f"(baseline {promo.get('baseline_hit_rate')}, "
                    f"with block {promo.get('with_sector_hit_rate') or promo.get('with_event_hit_rate')})."
                )
        elif item.get("status") == "pending_ablation" and block:
            row = ablation.get(block) or {}
            delta_pp = row.get("delta_pp")
            if delta_pp is not None and float(delta_pp) >= _ABLATION_ACCEPT_PP:
                item["status"] = "accepted"
            else:
                item["status"] = "rejected"
                item["rejection_reason"] = (
                    f"Walk-forward ablation delta {delta_pp} pp < {_ABLATION_ACCEPT_PP} pp gate "
                    f"(without block hit rate {row.get('direction_hit_rate_without_block')}, "
                    f"baseline {row.get('baseline_hit_rate')})."
                )
            item["ablation"] = row
        elif item.get("id") == "news_event_overlay" and item.get("status") == "pending_ablation":
            try:
                from trade_integrations.dataflows.index_research.news_event_features import (
                    load_news_model_config,
                )

                cfg = load_news_model_config()
                overlay_status = str(cfg.get("news_event_overlay") or "pending")
                item["status"] = overlay_status if overlay_status != "pending" else "pending_ablation"
                item["config"] = cfg.get("coverage", {}).get("gates")
            except Exception:
                pass
        resolved.append(item)
    return resolved


def _format_decisions_md(
    *,
    ticker: str,
    backtest: dict[str, Any] | None,
    counterfactual: dict[str, Any] | None,
    diagnostics: dict[str, Any] | None,
    t0_audit: dict[str, Any] | None,
    data_audit: dict[str, Any] | None,
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
    structural_changes = _resolve_structural_changes(diagnostics)
    for change in structural_changes:
        lines.append(f"### {change['id']} — {change['status']}")
        lines.append("")
        lines.append(f"**Hypothesis:** {change['hypothesis']}")
        if change["status"] == "rejected":
            lines.append(f"**Rejected because:** {change.get('rejection_reason')}")
        elif change.get("validation_note"):
            lines.append(f"**Validation:** {change['validation_note']}")
        elif change.get("promotion"):
            promo = change["promotion"]
            lines.append(
                f"**Promotion ablation:** baseline {promo.get('baseline_hit_rate')}, "
                f"with block {promo.get('with_sector_hit_rate') or promo.get('with_event_hit_rate')}, "
                f"delta {promo.get('delta_pp')} pp (gate {_ABLATION_ACCEPT_PP} pp)."
            )
        elif change.get("ablation"):
            row = change["ablation"]
            lines.append(
                f"**Ablation:** without {row.get('block')} block hit rate "
                f"{row.get('direction_hit_rate_without_block')} "
                f"(baseline {row.get('baseline_hit_rate')}, delta {row.get('delta_pp')} pp)."
            )
        else:
            if change["id"] == "delta_features":
                delta_row = ablation.get("delta") or {}
                lines.append(
                    f"**Ablation:** without delta block hit rate "
                    f"{delta_row.get('direction_hit_rate_without_block')} "
                    f"(baseline {delta_row.get('baseline_hit_rate')})."
                )
            if change["id"] == "dii_backfill":
                coverage_rows = (data_audit or {}).get("factor_coverage") or []
                dii = next((r for r in coverage_rows if r.get("factor") == "dii_net_5d"), {})
                flow_pct = dii.get("flow_era_coverage_pct")
                full_pct = dii.get("coverage_pct")
                lines.append(
                    f"**Data:** DII full-window coverage {full_pct}%; "
                    f"flow-era (≥{data_audit.get('flow_effective_start')}) {flow_pct}%."
                )
            if change["id"] == "scenario_shrinkage":
                lines.append(
                    f"**Measurement:** cap_artifact misses remain {cf_summary.get('cap_artifact_count')} "
                    f"after shrinkage (target ≤2)."
                )
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
    data_audit = load_data_audit_report(ticker)
    content = _format_decisions_md(
        ticker=ticker,
        backtest=backtest,
        counterfactual=counterfactual,
        diagnostics=diagnostics,
        t0_audit=t0_audit,
        data_audit=data_audit,
    )
    path = _DECISIONS_PATH(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def run_and_save_decisions(**kwargs: Any) -> dict[str, Any]:
    ticker = str(kwargs.get("ticker") or "NIFTY")
    path = generate_equation_improvement_decisions(ticker=ticker)
    return {"status": "ok", "path": str(path), "ticker": ticker}
