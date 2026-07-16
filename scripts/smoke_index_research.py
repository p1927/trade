#!/usr/bin/env python3
"""Smoke test for index research pipeline (OpenAlgo optional)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401

from trade_integrations.context.hub import save_index_research
from trade_integrations.dataflows.company_research.models import StageResult
from trade_integrations.dataflows.index_research.models import ConstituentSignal


def _mock_signals() -> list[ConstituentSignal]:
    return [
        ConstituentSignal(
            symbol="RELIANCE",
            weight=0.4,
            sector="Energy",
            sentiment_score=0.2,
            events=[{"type": "results", "date": "2026-07-20"}],
        ),
        ConstituentSignal(
            symbol="TCS",
            weight=0.3,
            sector="Information Technology",
            sentiment_score=-0.1,
            events=[{"type": "dividend", "date": "2026-07-18"}],
        ),
    ]


def _mock_macro_stage() -> StageResult:
    now = datetime.now(timezone.utc)
    return StageResult(
        stage="macro_global",
        status="ok",
        vendor="macro_global",
        fetched_at=now,
        data={
            "factors": {
                "usd_inr": 83.2,
                "oil_brent": 82.0,
                "india_vix": 14.5,
                "fii_net_5d": 500.0,
            },
            "factor_rows": [
                {"factor": "usd_inr", "value": 83.2, "source": "yfinance"},
                {"factor": "oil_brent", "value": 82.0, "source": "yfinance"},
            ],
        },
    )


def _check_doc(label: str, doc) -> bool:
    ok = True
    print(f"\n=== {label} ===")
    print(f"ticker={doc.ticker} spot={doc.spot} horizon={doc.horizon}")
    print(f"stages={[s.stage + ':' + s.status for s in doc.stages]}")
    print(f"prediction_view={doc.prediction.get('view')}")
    print(f"scenarios={len(doc.scenarios)}")
    contributors = (doc.factor_explanation or {}).get("contributors") or []
    if contributors:
        print(f"factor_explain={doc.factor_explanation.get('method')} contributors={len(contributors)}")
        for row in contributors[:3]:
            print(
                f"  - {row.get('label')}: {row.get('contribution_pct')}% macro "
                f"({row.get('share_of_macro', 0):.0%} of macro)"
            )
    print(f"sensitivity_curves={len(doc.factor_sensitivity or [])} event_curves={len(doc.event_impact_curves or [])}")
    if not doc.prediction:
        print("WARN: empty prediction")
        ok = False
    if not doc.constituent_signals:
        print("WARN: no constituent signals")
        ok = False
    return ok


def _run_mocked() -> bool:
    from trade_integrations.dataflows.index_research.aggregator import run_index_research

    with (
        patch(
            "trade_integrations.dataflows.index_research.aggregator.batch_constituent_research",
            return_value=_mock_signals(),
        ),
        patch(
            "trade_integrations.dataflows.index_research.aggregator.fetch_global_macro_snapshot",
            return_value=_mock_macro_stage(),
        ),
        patch(
            "trade_integrations.dataflows.index_research.aggregator._fetch_spot",
            return_value=24500.0,
        ),
        patch(
            "trade_integrations.dataflows.index_research.aggregator._nifty_trend_20d",
            return_value="sideways",
        ),
        patch(
            "trade_integrations.dataflows.index_research.aggregator.append_prediction",
            MagicMock(),
        ),
    ):
        doc = run_index_research("NIFTY", horizon_days=14)
    path = save_index_research(doc)
    print(f"Saved (mock): {path}")
    return _check_doc("NIFTY (mock)", doc)


def _run_live() -> bool:
    from trade_integrations.dataflows.index_research.aggregator import run_index_research

    doc = run_index_research("NIFTY")
    path = save_index_research(doc)
    print(f"Saved: {path}")
    ok = _check_doc("NIFTY", doc)
    if doc.recommended if hasattr(doc, "recommended") else False:
        pass
    if doc.prediction:
        print(
            json.dumps(
                {
                    "view": doc.prediction.get("view"),
                    "expected_return_pct": doc.prediction.get("expected_return_pct"),
                    "range": doc.prediction.get("range"),
                }
            )
        )
    return ok


def main() -> int:
    use_mock = "--mock" in sys.argv or "--offline" in sys.argv
    all_ok = _run_mocked() if use_mock else _run_live()
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
