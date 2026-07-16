"""Daily index factor snapshot — macro + constituent aggregates."""

from __future__ import annotations

from collections import defaultdict

from trade_integrations.dataflows.index_research.factor_store import (
    get_factor_data_dir,
    save_daily_factors,
)
from trade_integrations.dataflows.index_research.macro_global import (
    collect_global_factor_rows,
)
from trade_integrations.dataflows.index_research.models import ConstituentSignal
from trade_integrations.dataflows.index_research.sources.batch_constituents import (
    batch_constituent_research,
)

_EARNINGS_EVENT_TYPES = frozenset({"results", "earnings", "earnings_signal"})


def _is_earnings_event(event: dict) -> bool:
    event_type = str(event.get("type", "")).lower()
    if event_type in _EARNINGS_EVENT_TYPES:
        return True
    return "result" in event_type or "earning" in event_type


def _sector_breadth_mean_sentiment(signals: list[ConstituentSignal]) -> float | None:
    by_sector: dict[str, list[float]] = defaultdict(list)
    for signal in signals:
        if signal.sentiment_score is None or not signal.sector:
            continue
        by_sector[signal.sector].append(signal.sentiment_score)
    if not by_sector:
        return None
    sector_means = [sum(scores) / len(scores) for scores in by_sector.values()]
    return sum(sector_means) / len(sector_means)


def _earnings_events_14d_count(signals: list[ConstituentSignal]) -> int:
    return sum(
        1
        for signal in signals
        for event in signal.events
        if _is_earnings_event(event)
    )


def build_constituent_aggregate_rows(signals: list[ConstituentSignal]) -> list[dict]:
    """Build index-level aggregate factor rows from constituent signals."""
    rows: list[dict] = []
    breadth = _sector_breadth_mean_sentiment(signals)
    if breadth is not None:
        rows.append(
            {
                "factor": "sector_breadth_mean_sentiment",
                "value": breadth,
                "source": "constituent_aggregate",
            }
        )
    rows.append(
        {
            "factor": "earnings_events_14d_count",
            "value": float(_earnings_events_14d_count(signals)),
            "source": "constituent_aggregate",
        }
    )
    return rows


def run_snapshot(*, snapshot_date: str, skip_constituents: bool = False) -> dict:
    """Collect factors and persist a daily snapshot."""
    signals: list[ConstituentSignal] = []
    constituent_sentiments: list[float] | None = None

    if not skip_constituents:
        signals = batch_constituent_research(refresh=False)
        constituent_sentiments = [
            signal.sentiment_score
            for signal in signals
            if signal.sentiment_score is not None
        ]

    macro_rows = collect_global_factor_rows(
        constituent_sentiments=constituent_sentiments or None,
    )
    aggregate_rows = build_constituent_aggregate_rows(signals) if signals else []
    all_rows = macro_rows + aggregate_rows
    save_daily_factors(snapshot_date, all_rows)

    out_path = get_factor_data_dir() / f"{snapshot_date}.parquet"
    return {
        "date": snapshot_date,
        "factor_count": len(all_rows),
        "constituent_count": len(signals),
        "skip_constituents": skip_constituents,
        "factors": [row["factor"] for row in all_rows],
        "path": str(out_path),
    }
