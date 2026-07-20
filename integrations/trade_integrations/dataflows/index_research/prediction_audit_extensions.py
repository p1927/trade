#!/usr/bin/env python3
"""Extend prediction data audit with news, shock calibration, and debate archive."""

from __future__ import annotations

from typing import Any


def audit_news_pipeline(*, ticker: str = "NIFTY") -> dict[str, Any]:
    from trade_integrations.context.hub import count_agent_debate_history
    from trade_integrations.dataflows.index_research.news_event_features import (
        load_news_model_config,
    )
    from trade_integrations.dataflows.index_research.news_shock_calibration import (
        load_shock_calibration,
    )
    from trade_integrations.dataflows.index_research.prediction_algorithms.track_constants import (
        debate_backtest_eligible,
    )

    shock = load_shock_calibration(ticker) or {}
    topics = shock.get("topics") or {}
    config = load_news_model_config(ticker)
    debate_count = count_agent_debate_history(ticker)
    return {
        "news_model_config": config,
        "shock_calibration_topics": len(topics),
        "shock_reconciled_total": shock.get("reconciled_total"),
        "debate_history_count": debate_count,
        "debate_backtest_eligible": debate_backtest_eligible(ticker),
        "debate_archive_min_dates": 60,
    }
