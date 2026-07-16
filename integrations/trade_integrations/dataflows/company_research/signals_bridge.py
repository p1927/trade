"""Bridge hub research signals (Finverse, ED-ALPHA) into events and agent markdown."""

from __future__ import annotations

from typing import Any


def format_earnings_signal_section(data: dict[str, Any] | None) -> str:
    """Markdown section for US earnings beat/miss probability."""
    if not data:
        return "_Earnings signal not run (US equities only)._\n"
    beat = data.get("beat_probability")
    miss = data.get("miss_probability")
    if beat is None and miss is None:
        note = data.get("note") or data.get("reason") or "no consensus or Finverse data"
        return f"_Earnings signal unavailable: {note}_\n"
    lines = [
        f"- **Beat probability:** {float(beat) * 100:.1f}%" if beat is not None else "",
        f"- **Miss probability:** {float(miss) * 100:.1f}%" if miss is not None else "",
    ]
    if data.get("historical_beat_rate") is not None:
        lines.append(
            f"- **Historical beat rate:** {float(data['historical_beat_rate']) * 100:.1f}%"
        )
    if data.get("confidence"):
        lines.append(f"- **Confidence:** {data['confidence']}")
    if data.get("eps_consensus") is not None:
        lines.append(f"- **EPS consensus:** {data['eps_consensus']}")
    if data.get("source"):
        lines.append(f"- **Source:** {data['source']}")
    body = "\n".join(line for line in lines if line)
    return body + "\n" if body else "_No earnings signal fields._\n"


def format_corp_events_section(data: dict[str, Any] | None) -> str:
    """Markdown section for ED-ALPHA corp-event forecast."""
    if not data:
        return "_Corp-event forecast not run (US equities; requires ED_ALPHA_BASE_URL)._\n"
    status = str(data.get("status") or "unknown")
    lines = [f"- **Status:** {status}"]
    if data.get("company_name"):
        lines.append(f"- **Company:** {data['company_name']}")
    if data.get("total_score") is not None:
        lines.append(f"- **Event-risk score:** {data['total_score']}")
    if data.get("rank") is not None:
        lines.append(f"- **Rank in latest run:** #{data['rank']}")
    if data.get("predict_date"):
        lines.append(
            f"- **Prediction window:** {data['predict_date']} "
            f"(+{data.get('horizon_days', '?')} days)"
        )
    if data.get("detail"):
        lines.append(f"- **Note:** {data['detail']}")
    evidence = data.get("evidence") or []
    if evidence:
        lines.append("- **Top news signals:**")
        for item in evidence[:3]:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or item.get("summary") or "—")[:100]
            score = item.get("llm_score")
            suffix = f" (score {score})" if score is not None else ""
            lines.append(f"  - {title}{suffix}")
    event = data.get("event")
    if isinstance(event, dict) and event.get("form"):
        lines.append(
            f"- **Matched filing:** {event.get('form')} "
            f"{event.get('filing_date') or ''} items {event.get('items') or '—'}"
        )
    return "\n".join(lines) + "\n"


def _earnings_signal_event(signal: dict[str, Any]) -> dict[str, Any] | None:
    beat = signal.get("beat_probability")
    if beat is None:
        return None
    beat_f = float(beat)
    if beat_f >= 0.6:
        price_impact = "bullish"
        vol_impact = "elevated"
        description = f"Finverse beat probability {beat_f:.0%} — earnings upside bias"
    elif beat_f <= 0.45:
        price_impact = "bearish"
        vol_impact = "elevated"
        description = f"Finverse beat probability {beat_f:.0%} — earnings miss risk"
    else:
        price_impact = "uncertain"
        vol_impact = "elevated"
        description = f"Finverse beat probability {beat_f:.0%} — mixed earnings setup"
    return {
        "date": None,
        "type": "earnings_signal",
        "description": description,
        "source": signal.get("source") or "finverse:earnings_surprise",
        "impact_on_price": price_impact,
        "impact_on_vol": vol_impact,
        "beat_probability": beat_f,
        "signal_kind": "earnings_forecast",
    }


def _corp_events_signal_event(corp: dict[str, Any]) -> dict[str, Any] | None:
    status = str(corp.get("status") or "").lower()
    if status in {"", "not_found"}:
        return None
    score = corp.get("total_score")
    rank = corp.get("rank")
    if status == "no_data" and score is None:
        return {
            "date": corp.get("predict_date"),
            "type": "corp_event_watch",
            "description": corp.get("detail") or "ED-ALPHA connected; batch ingest pending",
            "source": "ed_alpha",
            "impact_on_price": "uncertain",
            "impact_on_vol": "moderate",
            "corp_event_score": None,
            "signal_kind": "corp_event_forecast",
        }
    if score is None:
        return None
    score_f = float(score)
    high_risk = score_f >= 100 or (rank is not None and int(rank) <= 50)
    return {
        "date": corp.get("predict_date"),
        "type": "corp_event_forecast",
        "description": (
            f"ED-ALPHA event-risk score {score_f:.0f}"
            + (f" (rank #{rank})" if rank is not None else "")
        ),
        "source": "ed_alpha",
        "impact_on_price": "uncertain" if not high_risk else "directional",
        "impact_on_vol": "elevated" if high_risk else "moderate",
        "corp_event_score": score_f,
        "corp_event_rank": rank,
        "signal_kind": "corp_event_forecast",
    }


def hub_signals_to_events(
    *,
    calendar_events: list[dict[str, Any]] | None = None,
    earnings_signal: dict[str, Any] | None = None,
    corp_events: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Merge calendar rows with Finverse / ED-ALPHA synthetic event signals."""
    merged: list[dict[str, Any]] = list(calendar_events or [])
    if earnings_signal:
        ev = _earnings_signal_event(earnings_signal)
        if ev:
            merged.append(ev)
    if corp_events:
        ev = _corp_events_signal_event(corp_events)
        if ev:
            merged.append(ev)
    return merged


def prediction_signals_from_hub(
    earnings_signal: dict[str, Any] | None = None,
    corp_events: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact dict for options ranker and prediction view."""
    signals: dict[str, Any] = {}
    if earnings_signal and earnings_signal.get("beat_probability") is not None:
        beat = float(earnings_signal["beat_probability"])
        signals["beat_probability"] = beat
        if beat >= 0.6:
            signals["earnings_bias"] = "bullish"
        elif beat <= 0.45:
            signals["earnings_bias"] = "bearish"
        else:
            signals["earnings_bias"] = "neutral"
    if corp_events:
        signals["corp_event_status"] = corp_events.get("status")
        if corp_events.get("total_score") is not None:
            signals["corp_event_score"] = float(corp_events["total_score"])
        if corp_events.get("rank") is not None:
            signals["corp_event_rank"] = int(corp_events["rank"])
    return signals
