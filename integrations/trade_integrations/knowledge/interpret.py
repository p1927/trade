"""Live factor snapshot → agent-readable interpretation (playbook-driven)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PLAYBOOK_DIR = Path(__file__).resolve().parent
_HIGH_FEAR_VIX = 18.0
_LOW_VIX = 14.0
_RSI_OVERBOUGHT = 70.0
_RSI_OVERSOLD = 30.0


@lru_cache(maxsize=1)
def load_factor_playbook() -> dict[str, Any]:
    path = _PLAYBOOK_DIR / "factor_playbook.yaml"
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return dict(payload.get("factors") or {})


@lru_cache(maxsize=1)
def load_strategy_playbook() -> dict[str, Any]:
    path = _PLAYBOOK_DIR / "strategy_playbook.yaml"
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return dict(payload.get("profiles") or {})


def _f(factors: dict[str, Any], key: str) -> float | None:
    raw = factors.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _trend_label(factors: dict[str, Any], trend_20d: str | None = None) -> str:
    if trend_20d in {"up", "down", "sideways"}:
        return trend_20d
    ret14 = _f(factors, "nifty_return_14d")
    if ret14 is None:
        return "sideways"
    if ret14 > 2.0:
        return "up"
    if ret14 < -2.0:
        return "down"
    return "sideways"


def _sector_dispersion(sector_breadth: dict[str, Any] | None) -> float | None:
    """Spread of sector sentiment means — high = rotation regime."""
    if not sector_breadth:
        return None
    by_sector = sector_breadth.get("by_sector") or {}
    if not isinstance(by_sector, dict) or len(by_sector) < 3:
        return None
    values = [float(v) for v in by_sector.values() if v is not None]
    if len(values) < 3:
        return None
    return max(values) - min(values)


def resolve_active_strategy_profile(
    factors: dict[str, Any],
    *,
    horizon_name: str = "B",
    trend_20d: str | None = None,
    sector_breadth: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pick best-matching strategy profile from playbook rules."""
    profiles = load_strategy_playbook()
    vix = _f(factors, "india_vix")
    rsi = _f(factors, "nifty_rsi_14")
    fii = _f(factors, "fii_net_5d")
    dii = _f(factors, "dii_net_5d")
    absorption = _f(factors, "dii_absorption_ratio")
    adx = _f(factors, "nifty_adx_14")
    ma200 = _f(factors, "nifty_ma200_distance_pct")
    ma50 = _f(factors, "nifty_ma50_distance_pct")
    inst = _f(factors, "institutional_net_5d")
    ret7 = _f(factors, "nifty_return_7d")
    realized_vol = _f(factors, "nifty_realized_vol_20d")
    tail_risk = _f(factors, "qfinindia_tail_risk")
    budget = factors.get("is_budget_week")
    results = factors.get("is_results_season")
    trend = _trend_label(factors, trend_20d)
    dispersion = _sector_dispersion(sector_breadth)

    scores: dict[str, float] = {key: 0.0 for key in profiles}

    if vix is not None and vix > _HIGH_FEAR_VIX:
        scores["mean_reversion"] += 2.0
        scores["defensive"] += 1.5
    if vix is not None and vix < _LOW_VIX and trend == "up":
        scores["momentum"] += 2.0
    if vix is not None and vix < _LOW_VIX and adx is not None and adx < 20:
        scores["low_vol_carry"] += 2.0
    if trend == "up" and (vix is None or vix < _HIGH_FEAR_VIX):
        scores["momentum"] += 1.5
    if rsi is not None and (rsi > _RSI_OVERBOUGHT or rsi < _RSI_OVERSOLD):
        scores["mean_reversion"] += 1.0
    if fii is not None and fii < 0 and dii is not None and dii > 0:
        scores["flow_driven"] += 2.0
    if absorption is not None and absorption > 1.0:
        scores["flow_driven"] += 1.0
    if budget in (1, True, "1", "true"):
        scores["event_vol"] += 2.0
    if results in (1, True, "1", "true"):
        scores["event_vol"] += 1.5
    if inst is not None and inst < 0 and vix is not None and vix > _HIGH_FEAR_VIX:
        scores["defensive"] += 2.0
    if realized_vol is not None and realized_vol > 18.0:
        scores["defensive"] += 1.0
    if tail_risk is not None and tail_risk > 0.5:
        scores["defensive"] += 1.0

    # Global risk-off: weak flows + down trend + elevated fear
    if trend == "down":
        scores["global_risk_off"] += 1.5
    if fii is not None and fii < -3000:
        scores["global_risk_off"] += 1.5
    if inst is not None and inst < -2000:
        scores["global_risk_off"] += 1.0
    if vix is not None and vix > _HIGH_FEAR_VIX and trend == "down":
        scores["global_risk_off"] += 1.5

    # Structural trend: horizon C + MA alignment
    if horizon_name == "C":
        scores["structural_trend"] += 1.5
    if ma200 is not None and ma200 > 0 and ma50 is not None and ma50 > 0:
        scores["structural_trend"] += 2.0
    if ma200 is not None and ma200 < -2.0:
        scores["structural_trend"] -= 1.0

    # Sector rotation: wide dispersion, not extreme VIX
    if dispersion is not None and dispersion > 0.25:
        scores["sector_rotation"] += 2.0
    if dispersion is not None and dispersion > 0.15 and adx is not None and adx < 25:
        scores["sector_rotation"] += 1.0
    if ret7 is not None and abs(ret7) < 2.0 and dispersion is not None and dispersion > 0.12:
        scores["sector_rotation"] += 0.5

    for key, profile in profiles.items():
        fit = profile.get("horizon_fit") or []
        if horizon_name in fit:
            scores[key] = scores.get(key, 0.0) + 0.5

    best_key = max(scores, key=lambda k: scores[k])
    if scores[best_key] <= 0:
        best_key = "momentum" if trend == "up" else "mean_reversion"

    profile = dict(profiles.get(best_key) or {})
    return {
        "key": best_key,
        "label": profile.get("label", best_key),
        "score": round(scores[best_key], 2),
        "when": profile.get("when"),
        "logic": profile.get("logic"),
        "risks": profile.get("risks"),
        "options_handoff": profile.get("options_handoff"),
        "indicators_to_watch": profile.get("indicators_to_watch") or [],
        "risk_notes": profile.get("risk_notes") or [],
        "conflicts_with": profile.get("conflicts_with") or [],
    }


def build_strategy_context_string(profile: dict[str, Any]) -> str:
    """QuantMuse-style named strategy context for LLM injection."""
    label = profile.get("label") or profile.get("key") or "index"
    parts = [f"{label} strategy profile"]
    if profile.get("when"):
        parts.append(f"When: {profile['when']}")
    if profile.get("logic"):
        parts.append(f"Logic: {profile['logic']}")
    if profile.get("risks"):
        parts.append(f"Risks: {profile['risks']}")
    if profile.get("options_handoff"):
        parts.append(f"Options handoff: {profile['options_handoff']}")
    watch = profile.get("indicators_to_watch") or []
    if watch:
        parts.append(f"Watch: {', '.join(str(w) for w in watch)}")
    return " | ".join(parts)


def _interpret_rsi(factors: dict[str, Any]) -> str | None:
    rsi = _f(factors, "nifty_rsi_14")
    vix = _f(factors, "india_vix")
    if rsi is None:
        return None
    if rsi > _RSI_OVERBOUGHT:
        if vix is not None and vix > _HIGH_FEAR_VIX:
            return f"RSI {rsi:.1f} overbought in high-VIX regime → mean-reversion risk elevated."
        return f"RSI {rsi:.1f} elevated; in low-VIX regimes momentum can persist."
    if rsi < _RSI_OVERSOLD:
        return f"RSI {rsi:.1f} oversold → bounce watch; confirm MACD histogram."
    return f"RSI {rsi:.1f} neutral mid-zone."


def _interpret_macd(factors: dict[str, Any]) -> str | None:
    hist = _f(factors, "nifty_macd_histogram")
    if hist is None:
        return None
    if hist > 0:
        return f"MACD histogram positive ({hist:.2f}) → short-term bullish impulse."
    if hist < 0:
        return f"MACD histogram negative ({hist:.2f}) → momentum fading."
    return "MACD histogram near zero → direction indecisive."


def build_technical_interpretation(
    factors: dict[str, Any],
    *,
    trend_20d: str | None = None,
) -> str:
    """One-paragraph TA interpretation for agent injection."""
    parts: list[str] = []
    trend = _trend_label(factors, trend_20d)
    parts.append(f"20d trend: {trend}.")

    rsi_note = _interpret_rsi(factors)
    if rsi_note:
        parts.append(rsi_note)

    macd_note = _interpret_macd(factors)
    if macd_note:
        parts.append(macd_note)

    ma20 = _f(factors, "nifty_ma20_distance_pct")
    if ma20 is not None:
        parts.append(f"Price {ma20:+.2f}% vs 20d MA.")

    vix = _f(factors, "india_vix")
    if vix is not None:
        regime = "high fear" if vix > _HIGH_FEAR_VIX else ("low vol" if vix < _LOW_VIX else "normal vol")
        parts.append(f"India VIX {vix:.1f} ({regime}).")

    bb = _f(factors, "nifty_bb_percent_b")
    if bb is not None:
        if bb > 1.0:
            parts.append(f"Bollinger %B {bb:.2f} — above upper band (stretched).")
        elif bb < 0.0:
            parts.append(f"Bollinger %B {bb:.2f} — below lower band (oversold).")

    bb_width = _f(factors, "nifty_bb_width_pct")
    if bb_width is not None and bb_width < 8.0:
        parts.append(f"Bollinger squeeze (width {bb_width:.1f}%) — breakout risk ahead.")

    stoch_k = _f(factors, "nifty_stoch_k")
    if stoch_k is not None:
        if stoch_k > 80:
            parts.append(f"Stochastic %K {stoch_k:.0f} overbought.")
        elif stoch_k < 20:
            parts.append(f"Stochastic %K {stoch_k:.0f} oversold.")

    williams = _f(factors, "nifty_williams_r")
    if williams is not None and williams > -20:
        parts.append(f"Williams %R {williams:.0f} — overbought zone.")

    cci = _f(factors, "nifty_cci_20")
    if cci is not None:
        if cci > 100:
            parts.append(f"CCI {cci:.0f} extended above +100.")
        elif cci < -100:
            parts.append(f"CCI {cci:.0f} extended below -100.")

    macd_line = _f(factors, "nifty_macd_line")
    macd_signal = _f(factors, "nifty_macd_signal")
    if macd_line is not None and macd_signal is not None:
        cross = "above" if macd_line > macd_signal else "below"
        parts.append(f"MACD line {cross} signal ({macd_line:.1f} vs {macd_signal:.1f}).")

    atr = _f(factors, "nifty_atr_pct")
    if atr is not None and atr > 1.5:
        parts.append(f"ATR {atr:.2f}% elevated — widen tactical stops.")

    return " ".join(parts)


def factor_notes_for_snapshot(factors: dict[str, Any], *, limit: int = 6) -> dict[str, str]:
    """Short pedagogy snippets for top present factors."""
    playbook = load_factor_playbook()
    notes: dict[str, str] = {}
    for key in factors:
        if key not in playbook:
            continue
        entry = playbook[key]
        notes[key] = str(entry.get("summary") or "")
        if len(notes) >= limit:
            break
    return notes


def build_index_interpretation_bundle(
    factors: dict[str, Any],
    *,
    horizon_name: str = "B",
    horizon_days: int = 14,
    trend_20d: str | None = None,
    prediction: dict[str, Any] | None = None,
    sector_breadth: dict[str, Any] | None = None,
    ticker: str = "NIFTY",
) -> dict[str, Any]:
    """Structured block for hub context and quant review."""
    from trade_integrations.knowledge.factor_trust import enrich_factor_notes_with_trust

    technical_keys = (
        "nifty_rsi_14",
        "nifty_ma20_distance_pct",
        "nifty_ma50_distance_pct",
        "nifty_ma200_distance_pct",
        "nifty_macd_line",
        "nifty_macd_signal",
        "nifty_macd_histogram",
        "nifty_bb_percent_b",
        "nifty_bb_width_pct",
        "nifty_stoch_k",
        "nifty_stoch_d",
        "nifty_williams_r",
        "nifty_cci_20",
        "nifty_adx_14",
        "nifty_atr_pct",
        "nifty_return_7d",
        "nifty_return_14d",
        "india_vix",
        "nifty_realized_vol_20d",
    )
    technical_readings = {
        key: factors[key]
        for key in technical_keys
        if factors.get(key) is not None
    }
    profile = resolve_active_strategy_profile(
        factors,
        horizon_name=horizon_name,
        trend_20d=trend_20d,
        sector_breadth=sector_breadth,
    )
    risk_notes = list(profile.get("risk_notes") or [])
    atr = _f(factors, "nifty_atr_pct")
    if atr is not None and profile.get("key") == "defensive":
        risk_notes.append(f"1d VaR proxy ≈ {atr:.2f}% of spot (ATR-based hedge sizing).")
    tail = _f(factors, "qfinindia_tail_risk")
    if tail is not None and tail > 0.4:
        risk_notes.append(f"Options tail-risk elevated ({tail:.2f}) — favor hedges over short vol.")

    return {
        "horizon_days": horizon_days,
        "horizon_name": horizon_name,
        "technical_readings": technical_readings,
        "technical_interpretation": build_technical_interpretation(factors, trend_20d=trend_20d),
        "active_strategy_profile": profile.get("key"),
        "strategy_profile": profile,
        "strategy_context": build_strategy_context_string(profile),
        "strategy_rationale": profile.get("logic"),
        "strategy_when": profile.get("when"),
        "strategy_risks": profile.get("risks"),
        "strategy_options_handoff": profile.get("options_handoff"),
        "indicators_to_watch": profile.get("indicators_to_watch") or [],
        "risk_notes": risk_notes,
        "factor_notes": enrich_factor_notes_with_trust(factors, ticker=ticker),
        "prediction_view": (prediction or {}).get("view"),
    }


def detect_forecast_disagreements(
    factors: dict[str, Any],
    prediction: dict[str, Any] | None,
    *,
    trend_20d: str | None = None,
) -> list[dict[str, str]]:
    """Rule-based conflicts between Ridge headline and live TA/flows."""
    if not prediction:
        return []

    disagreements: list[dict[str, str]] = []
    view = str(prediction.get("view") or "").lower()
    bullish = view in {"bullish", "bull", "up", "positive"}
    bearish = view in {"bearish", "bear", "down", "negative"}

    hist = _f(factors, "nifty_macd_histogram")
    if hist is not None:
        if bullish and hist < -5:
            disagreements.append(
                {
                    "type": "ta_momentum",
                    "detail": "Model bullish but MACD histogram negative — short-term momentum fading.",
                }
            )
        if bearish and hist > 5:
            disagreements.append(
                {
                    "type": "ta_momentum",
                    "detail": "Model bearish but MACD histogram positive — tactical bounce risk.",
                }
            )

    rsi = _f(factors, "nifty_rsi_14")
    vix = _f(factors, "india_vix")
    if rsi is not None and vix is not None and vix > _HIGH_FEAR_VIX:
        if bullish and rsi > _RSI_OVERBOUGHT:
            disagreements.append(
                {
                    "type": "regime_mean_reversion",
                    "detail": f"Model bullish with RSI {rsi:.0f} in high-VIX regime — mean-reversion playbook conflicts.",
                }
            )

    fii = _f(factors, "fii_net_5d")
    if bearish and fii is not None and fii > 5000:
        disagreements.append(
            {
                "type": "flows",
                "detail": "Model bearish but FII 5d net strongly positive — flow impulse disagrees.",
            }
        )
    if bullish and fii is not None and fii < -5000:
        disagreements.append(
            {
                "type": "flows",
                "detail": "Model bullish but FII 5d net strongly negative — foreign selling headwind.",
            }
        )

    profile = resolve_active_strategy_profile(factors, trend_20d=trend_20d)
    if bullish and profile.get("key") == "defensive":
        disagreements.append(
            {
                "type": "strategy_profile",
                "detail": "Model bullish but defensive strategy profile active (high fear + weak flows).",
            }
        )

    return disagreements


def build_surprises(
    factors: dict[str, Any],
    prediction: dict[str, Any] | None,
    *,
    trend_20d: str | None = None,
) -> list[dict[str, str]]:
    """Blind-spot items the main pipeline may underweight."""
    surprises: list[dict[str, str]] = []
    disagreements = detect_forecast_disagreements(factors, prediction, trend_20d=trend_20d)
    for row in disagreements:
        surprises.append({"kind": "disagreement", "message": row["detail"], "category": row["type"]})

    skew = _f(factors, "qfinindia_skew")
    if skew is not None and skew < -0.05:
        surprises.append(
            {
                "kind": "derivatives",
                "message": f"Put skew elevated ({skew:.3f}) — downside hedging demand not in price momentum alone.",
                "category": "skew",
            }
        )

    em = _f(factors, "qfinindia_expected_move")
    if em is not None and em > 3.0:
        surprises.append(
            {
                "kind": "derivatives",
                "message": f"Options-implied expected move ±{em:.1f}% — widen tactical range vs point forecast.",
                "category": "expected_move",
            }
        )

    absorption = _f(factors, "dii_absorption_ratio")
    fii = _f(factors, "fii_net_5d")
    if absorption is not None and absorption > 1.2 and fii is not None and fii < 0:
        surprises.append(
            {
                "kind": "flows",
                "message": "DII fully absorbing FII selling — index may hold range despite foreign outflows.",
                "category": "absorption",
            }
        )

    return surprises[:8]
