"""Merge TradingAgents debate output with quantitative forecasts."""

from __future__ import annotations

import re
from typing import Any


def _parse_pct(text: str) -> float | None:
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _parse_price(text: str) -> float | None:
    m = re.search(r"(?:₹|rs\.?|inr)\s*([0-9,]+(?:\.\d+)?)", text, re.I)
    if not m:
        m = re.search(r"\b([0-9]{3,5}(?:\.\d{1,2})?)\b", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _view_from_text(text: str) -> str | None:
    lower = text.lower()
    if any(w in lower for w in ("bullish", "buy", "accumulate", "long")):
        return "bullish"
    if any(w in lower for w in ("bearish", "sell", "short", "avoid")):
        return "bearish"
    if "neutral" in lower or "hold" in lower or "sideways" in lower:
        return "neutral"
    return None


def extract_structured_debate(debate_json: dict[str, Any] | None) -> dict[str, Any]:
    """Best-effort structured forecast from agent_debate/latest.json."""
    if not debate_json:
        return {}

    rating = debate_json.get("rating")
    direction_confidence = None
    if isinstance(rating, (int, float)):
        direction_confidence = round(min(max(float(rating) / 10.0, 0.2), 0.95), 3)
    elif isinstance(rating, str):
        try:
            direction_confidence = round(min(max(float(rating) / 10.0, 0.2), 0.95), 3)
        except ValueError:
            pass

    final = str(debate_json.get("final_trade_decision") or "")
    inv = debate_json.get("investment_debate") or {}
    judge = str(inv.get("judge_decision") or "")
    combined = f"{final}\n{judge}"

    view = _view_from_text(combined)
    expected_return_pct = _parse_pct(combined)

    target = None
    stop = None
    lower_combined = combined.lower()
    t_idx = lower_combined.find("target")
    if t_idx >= 0:
        target = _parse_price(combined[t_idx : t_idx + 50])
    if target is None:
        target = _parse_price(combined)
    for label in ("stop", "stop-loss", "stop loss"):
        idx = lower_combined.find(label)
        if idx >= 0:
            stop = _parse_price(combined[idx : idx + 40])
            break

    catalysts: list[str] = []
    for key in ("market", "sentiment", "news", "fundamentals"):
        report = (debate_json.get("analyst_reports") or {}).get(key)
        if report and isinstance(report, str) and len(report) > 20:
            catalysts.append(key)

    return {
        "view": view,
        "direction_confidence": direction_confidence,
        "expected_return_pct": expected_return_pct,
        "target": target,
        "stop": stop,
        "catalysts": catalysts[:5],
        "rationale": (final or judge)[:500],
        "debate_as_of": debate_json.get("as_of"),
    }


def merge_stock_prediction(
    debate: dict[str, Any] | None,
    quant: dict[str, Any],
    *,
    spot: float,
    horizon_days: int = 14,
) -> dict[str, Any]:
    """
    Hybrid C merge: debate direction/catalysts; quant primary for range bands.

    Returns prediction dict with provenance block.
    """
    d = debate or {}
    q = quant or {}
    q_range = q.get("range") or {}
    q_low = q_range.get("low")
    q_high = q_range.get("high")
    q_ret = float(q.get("expected_return_pct") or 0.0)
    d_ret = d.get("expected_return_pct")
    d_conf = float(d.get("direction_confidence") or 0.0)
    q_conf = float(q.get("model_confidence") or 0.0)

    view = d.get("view") or q.get("view") or "neutral"
    if d_conf < 0.4 and q.get("view"):
        view = q.get("view")

    if d_ret is not None and q_ret is not None:
        expected_return_pct = round(0.6 * float(d_ret) + 0.4 * q_ret, 3)
    elif d_ret is not None:
        expected_return_pct = round(float(d_ret), 3)
    else:
        expected_return_pct = round(q_ret, 3)

    low, high = q_low, q_high
    if low is not None and high is not None and d_ret is not None:
        # Debate narrows/widens band when strong directional signal
        spread = high - low
        adjust = spread * 0.15 * (1 if float(d_ret) >= 0 else -1)
        low = round(low + adjust * 0.5, 2)
        high = round(high + adjust * 0.5, 2)

    target = d.get("target")
    stop = d.get("stop")
    if target is None and high is not None:
        target = high
    if stop is None and low is not None:
        stop = low

    confidence = round(min(d_conf or q_conf or 0.5, q_conf or d_conf or 0.5), 3)
    if d_conf and q_conf:
        confidence = round(min(d_conf, q_conf), 3)

    return {
        "view": view,
        "horizon_days": horizon_days,
        "expected_return_pct": expected_return_pct,
        "range": {"low": low, "high": high},
        "target": target,
        "stop": stop,
        "confidence": confidence,
        "catalysts": d.get("catalysts") or [],
        "rationale": d.get("rationale"),
        "provenance": {
            "direction": "debate" if d.get("view") else "quant",
            "range": "quant",
            "targets": "debate" if d.get("target") else "derived",
            "debate_as_of": d.get("debate_as_of"),
            "quant_source": q.get("source"),
        },
        "quant": {
            "expected_return_pct": q_ret,
            "model_confidence": q_conf,
            "volatility_annual_pct": q.get("volatility_annual_pct"),
        },
        "debate": {
            "direction_confidence": d_conf,
            "expected_return_pct": d_ret,
        },
    }


def merge_index_prediction(
    debate: dict[str, Any] | None,
    index_doc_prediction: dict[str, Any],
) -> dict[str, Any]:
    """Reconcile index predictor with debate direction when debate present."""
    d = debate or {}
    base = dict(index_doc_prediction or {})
    if not d:
        return base
    if d.get("view"):
        base["view"] = d["view"]
    prov = dict(base.get("provenance") or {})
    prov["direction"] = "debate"
    prov["debate_as_of"] = d.get("debate_as_of")
    base["provenance"] = prov
    if d.get("direction_confidence") is not None:
        base["confidence"] = min(
            float(base.get("confidence") or 1.0),
            float(d["direction_confidence"]),
        )
    return base
