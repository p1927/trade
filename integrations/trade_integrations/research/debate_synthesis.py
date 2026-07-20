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
    if any(
        w in lower
        for w in (
            "bearish",
            "sell",
            "short",
            "avoid",
            "underweight",
            "trim",
            "reduce exposure",
            "reduce holdings",
            "exit",
        )
    ):
        return "bearish"
    if any(w in lower for w in ("bullish", "buy", "accumulate", "long", "overweight", "add exposure")):
        return "bullish"
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
    """Hybrid merge: debate direction/catalysts; quant primary for macro attribution."""
    from trade_integrations.dataflows.index_research.views import classify_index_view

    d = debate or {}
    base = dict(index_doc_prediction or {})
    if not d:
        return base

    q_ret = float(base.get("expected_return_pct") or 0.0)
    d_ret = d.get("expected_return_pct")
    d_conf = float(d.get("direction_confidence") or 0.0)
    q_conf_raw = base.get("direction_confidence")
    try:
        q_conf = float(q_conf_raw) if q_conf_raw is not None else 0.0
    except (TypeError, ValueError):
        q_conf = 0.0

    if d_ret is not None:
        expected_return_pct = round(0.6 * float(d_ret) + 0.4 * q_ret, 4)
    else:
        expected_return_pct = round(q_ret, 4)

    bottom_up = float(base.get("bottom_up_return_pct") or 0.0)
    from trade_integrations.dataflows.index_research.predictor import cap_macro_delta

    macro_delta = cap_macro_delta(expected_return_pct - bottom_up)

    view = classify_index_view(expected_return_pct)

    confidence = round(min(d_conf or q_conf or 0.5, q_conf or d_conf or 0.5), 3)
    if d_conf and q_conf:
        confidence = round(min(d_conf, q_conf), 3)

    merged = dict(base)
    merged["expected_return_pct"] = expected_return_pct
    merged["macro_delta_pct"] = macro_delta
    merged["view"] = view
    merged["direction_view"] = view
    merged["confidence"] = confidence
    merged["direction_confidence"] = confidence
    merged["provenance"] = {
        **dict(base.get("provenance") or {}),
        "direction": "debate" if d.get("view") else "quant",
        "debate_as_of": d.get("debate_as_of"),
    }
    merged["quant"] = {
        "expected_return_pct": q_ret,
        "macro_delta_pct": base.get("macro_delta_pct"),
        "direction_confidence": q_conf,
        "view": base.get("view"),
    }
    merged["debate"] = {
        "direction_confidence": d_conf,
        "expected_return_pct": d_ret,
        "view": d.get("view"),
    }
    if d.get("rationale"):
        merged["debate_rationale"] = d["rationale"][:300]
    merged["debate_merged"] = True
    return merged


def apply_debate_bias_to_stock_ranked(
    ranked: list[dict[str, Any]],
    *,
    debate_view: str | None,
    debate_confidence: float = 0.0,
) -> list[dict[str, Any]]:
    """Adjust stock strategy scores from TradingAgents debate direction."""
    if not ranked or not debate_view:
        return ranked
    boost = min(0.25, 0.1 + debate_confidence * 0.15)
    out: list[dict[str, Any]] = []
    for row in ranked:
        item = dict(row)
        name = str(item.get("name") or "")
        score = float(item.get("score") or 0.5)
        if debate_view == "bearish":
            if name in ("buy_dip", "momentum_breakout", "event_play"):
                score -= boost
            elif name == "hold_cash":
                score += boost
        elif debate_view == "bullish":
            if name in ("buy_dip", "momentum_breakout", "event_play"):
                score += boost * 0.8
            elif name == "hold_cash":
                score -= boost * 0.5
        item["score"] = round(min(max(score, 0.2), 0.95), 3)
        if debate_view == "bearish" and name == "hold_cash" and item["score"] >= 0.55:
            item["tier"] = "Recommended"
        out.append(item)
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out


def _options_name_bias(name: str, debate_view: str) -> float:
    n = name.lower()
    if debate_view == "bullish":
        if "bull" in n or ("call" in n and "spread" in n):
            return 0.12
        if "bear" in n or "put" in n:
            return -0.08
    elif debate_view == "bearish":
        if "bear" in n or ("put" in n and "spread" in n):
            return 0.12
        if "bull" in n or "call" in n:
            return -0.08
    elif debate_view == "neutral":
        if "condor" in n or "iron" in n:
            return 0.06
    return 0.0


def merge_options_context(
    debate: dict[str, Any] | None,
    options_doc: Any,
) -> dict[str, Any]:
    """
    Bias options ranked strategy scores from debate; enrich prediction provenance.

    Does not replace chain analytics — adjusts ranker output only.
    """
    d = debate or {}
    from dataclasses import asdict, is_dataclass

    if is_dataclass(options_doc):
        base = asdict(options_doc)
    elif isinstance(options_doc, dict):
        base = dict(options_doc)
    else:
        base = getattr(options_doc, "__dict__", {}) or {}

    ranked = [dict(r) for r in (base.get("ranked_strategies") or [])]
    prediction = dict(base.get("prediction") or {})
    view = d.get("view")
    conf = float(d.get("direction_confidence") or 0.0)
    top: dict[str, Any] = {}

    if view and ranked:
        boost = min(0.2, 0.08 + conf * 0.12)
        for row in ranked:
            delta = _options_name_bias(str(row.get("name") or ""), view) * (1 + boost)
            row["score"] = round(min(max(float(row.get("score") or 0.5) + delta, 0.2), 0.95), 3)
            row["debate_bias"] = round(delta, 3)
        ranked.sort(key=lambda r: r.get("score", 0), reverse=True)
        top = ranked[0]

    if view:
        prediction["debate_view"] = view
        prov = dict(prediction.get("provenance") or {})
        prov["direction"] = "debate"
        prov["debate_as_of"] = d.get("debate_as_of")
        prediction["provenance"] = prov
        if d.get("rationale"):
            prediction["debate_rationale"] = d["rationale"][:300]

    recommended = dict(base.get("recommended") or {})
    if ranked and top:
        if recommended.get("name") != top.get("name"):
            recommended = {
                "name": top.get("name"),
                "score": top.get("score"),
                "tier": top.get("tier"),
                "rationale": top.get("rationale"),
                "legs": top.get("legs") or [],
                "max_profit": top.get("max_profit"),
                "max_loss": top.get("max_loss"),
                "net_max_profit": top.get("net_max_profit"),
                "net_max_loss": top.get("net_max_loss"),
                "net_debit_credit": top.get("net_debit_credit"),
                "breakevens": top.get("breakevens"),
            }

    return {
        "ranked_strategies": ranked,
        "recommended": recommended,
        "prediction": prediction,
        "payoff": top.get("payoff") or base.get("payoff"),
        "charges": top.get("charges") or base.get("charges"),
        "payoff_over_time": top.get("payoff_over_time") or base.get("payoff_over_time"),
    }
