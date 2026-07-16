"""Render IndexResearchDoc as markdown."""

from __future__ import annotations

from .models import IndexResearchDoc


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.2f}"


def format_index_report(doc: IndexResearchDoc) -> str:
    count = len(doc.constituent_signals or [])
    prediction = doc.prediction or {}
    range_block = prediction.get("range") or {}
    drivers = prediction.get("top_drivers") or []

    parts = [
        f"# Index Research — {doc.ticker}",
        "",
        f"**As of:** {doc.as_of.isoformat()}",
        "",
        f"**Horizon:** {doc.horizon.get('name', 'B')} ({doc.horizon.get('days', 14)} days)",
        f"**Spot:** {_fmt_price(doc.spot)}",
        f"**Constituents analyzed:** {count}",
        "",
        "## Prediction",
        "",
        f"- **View:** {prediction.get('view', 'n/a')}",
        f"- **Expected return:** {_fmt_pct(prediction.get('expected_return_pct'))}",
        f"- **Range:** {_fmt_price(range_block.get('low'))} – {_fmt_price(range_block.get('high'))}",
        f"- **Bottom-up:** {_fmt_pct(prediction.get('bottom_up_return_pct'))}",
        f"- **Macro delta:** {_fmt_pct(prediction.get('macro_delta_pct'))}",
    ]

    if drivers:
        parts.extend(["", "### Top drivers", ""])
        for driver in drivers[:5]:
            symbol = driver.get("symbol", "?")
            contribution = driver.get("contribution_to_index_pct")
            parts.append(f"- **{symbol}:** {_fmt_pct(contribution)}")

    regime = doc.regime or {}
    if regime:
        parts.extend(
            [
                "",
                "## Regime",
                "",
                f"- **Label:** {regime.get('label', 'n/a')}",
                f"- **India VIX:** {regime.get('india_vix', 'n/a')}",
                f"- **20d trend:** {regime.get('trend_20d', 'n/a')}",
            ]
        )

    if doc.scenarios:
        parts.extend(["", "## Scenarios", ""])
        for scenario in doc.scenarios[:6]:
            event = scenario.get("event", "event")
            outcome = scenario.get("outcome", "outcome")
            index_range = scenario.get("index_range") or []
            if len(index_range) == 2:
                range_text = f"{index_range[0]:,.0f} – {index_range[1]:,.0f}"
            else:
                range_text = "n/a"
            prob = scenario.get("probability")
            prob_text = f"{prob:.0%}" if isinstance(prob, (int, float)) else "n/a"
            parts.append(f"- **{event} / {outcome}:** {range_text} (p={prob_text})")

    accuracy = doc.accuracy or {}
    if accuracy.get("sample_count"):
        hit_14d = accuracy.get("direction_hit_rate_14d")
        hit_text = f"{hit_14d:.0%}" if isinstance(hit_14d, (int, float)) else "n/a"
        parts.extend(
            [
                "",
                "## Accuracy (ledger)",
                "",
                f"- **Samples:** {accuracy.get('sample_count')}",
                f"- **MAE (14d):** {_fmt_pct(accuracy.get('mae_14d_pct'))}",
                f"- **Direction hit rate (14d):** {hit_text}",
            ]
        )

    return "\n".join(parts) + "\n"
