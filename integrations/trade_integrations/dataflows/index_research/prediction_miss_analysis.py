"""Root-cause analysis for wrong index predictions (T0 vs maturity factor diff)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.backtest_runner import (
    _FACTOR_LABELS,
    _calendar_events_for_date,
    _row_factor_dict,
    run_walk_forward_backtest,
)
from trade_integrations.dataflows.index_research.causal_attribution import (
    _fetch_index_headlines,
    _headline_tags,
    build_causal_hypotheses,
    collect_constituent_headlines_for_day,
)
from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents
from trade_integrations.dataflows.index_research.factor_matrix import MACRO_FACTOR_KEYS
from trade_integrations.dataflows.index_research.horizon_dates import (
    resolve_maturity_trading_date,
)
from trade_integrations.dataflows.index_research.predictor import _MACRO_DELTA_CAP_PCT
from trade_integrations.dataflows.index_research.sources.history_loader import load_aligned_factor_history

_NEUTRAL_THRESHOLD_PCT = 0.15
_REGIME_FACTORS = frozenset(
    {
        "nifty_return_7d",
        "nifty_return_14d",
        "constituent_momentum_7d",
        "fii_net_5d",
        "india_vix",
        "index_sentiment",
        "sector_breadth_mean_sentiment",
    }
)
_CRITICAL_FACTORS = frozenset({"oil_brent", "india_vix", "nifty_return_7d", "fii_net_5d"})
_EVENT_TAGS = frozenset({"war", "oil", "rbi", "fii", "us", "earnings"})


def _miss_report_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "miss_analysis_latest.json"


def save_miss_analysis_report(report: dict[str, Any], *, ticker: str = "NIFTY") -> Path:
    path = _miss_report_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path


def load_miss_analysis_report(ticker: str = "NIFTY") -> dict[str, Any] | None:
    path = _miss_report_path(ticker)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _feature_columns(frame: pd.DataFrame) -> list[str]:
    exclude = {"date", "date_str", "close", "open", "high", "low", "volume", "target", "realized_1d_pct"}
    return [
        c
        for c in frame.columns
        if c not in exclude and pd.api.types.is_numeric_dtype(frame[c])
    ]


def _trading_dates(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []
    return frame["date"].astype(str).str[:10].tolist()


def resolve_maturity_date(
    prediction_date: str,
    horizon_days: int,
    trading_dates: list[str],
) -> str | None:
    """Trading date ``horizon_days`` sessions after ``prediction_date``."""
    return resolve_maturity_trading_date(prediction_date, horizon_days, trading_dates)


def factor_snapshot_at(
    day: str,
    frame: pd.DataFrame,
    feature_cols: list[str],
    *,
    keys: tuple[str, ...] | None = None,
) -> dict[str, float]:
    matches = frame.index[frame["date"].astype(str).str[:10] == day[:10]].tolist()
    if not matches:
        return {}
    row = frame.iloc[int(matches[0])]
    factors = _row_factor_dict(row, feature_cols)
    if keys:
        return {k: factors[k] for k in keys if k in factors}
    return factors


def compute_factor_delta_horizon(
    t0: dict[str, float],
    t1: dict[str, float],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    drivers: list[dict[str, Any]] = []
    for key in set(t0) | set(t1):
        v0 = t0.get(key)
        v1 = t1.get(key)
        if v0 is None or v1 is None:
            continue
        delta = float(v1) - float(v0)
        if v0 == 0:
            pct = 0.0 if delta == 0 else 100.0
        else:
            pct = delta / abs(float(v0)) * 100.0
        drivers.append(
            {
                "factor": key,
                "label": _FACTOR_LABELS.get(key, key),
                "t0": round(float(v0), 4),
                "t1": round(float(v1), 4),
                "delta": round(delta, 4),
                "change_pct": round(pct, 2),
            }
        )
    drivers.sort(key=lambda d: abs(d["delta"]), reverse=True)
    return drivers[:limit]


def horizon_price_path(
    frame: pd.DataFrame,
    prediction_date: str,
    maturity_date: str,
) -> list[dict[str, Any]]:
    if frame.empty or "close" not in frame.columns:
        return []
    sub = frame[
        (frame["date"].astype(str).str[:10] >= prediction_date[:10])
        & (frame["date"].astype(str).str[:10] <= maturity_date[:10])
    ].sort_values("date")
    if sub.empty:
        return []

    start_close = float(sub.iloc[0]["close"])
    rows: list[dict[str, Any]] = []
    prev = start_close
    for _, row in sub.iterrows():
        close = float(row["close"])
        day = str(row["date"])[:10]
        cum_pct = (close - start_close) / start_close * 100.0 if start_close else 0.0
        daily_pct = (close - prev) / prev * 100.0 if prev else 0.0
        rows.append(
            {
                "date": day,
                "close": round(close, 2),
                "daily_return_pct": round(daily_pct, 3),
                "cumulative_return_pct": round(cum_pct, 3),
            }
        )
        prev = close
    return rows


def _constituent_movers_horizon(
    prediction_date: str,
    maturity_date: str,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    try:
        import yfinance as yf
    except ImportError:
        return []

    weight_map = {row.symbol.strip().upper(): float(row.weight) for row in load_nifty50_constituents()}
    movers: list[dict[str, Any]] = []
    for sym, weight in weight_map.items():
        yf_sym = sym if sym.endswith(".NS") else f"{sym}.NS"
        try:
            start = (date.fromisoformat(prediction_date[:10]) - timedelta(days=5)).isoformat()
            end = (date.fromisoformat(maturity_date[:10]) + timedelta(days=3)).isoformat()
            hist = yf.Ticker(yf_sym).history(start=start, end=end, auto_adjust=True)
        except Exception:
            continue
        if hist is None or hist.empty:
            continue
        close_col = "Close" if "Close" in hist.columns else "close"
        closes = hist[close_col].astype(float)
        closes.index = closes.index.tz_localize(None) if hasattr(closes.index, "tz") else closes.index
        t0_ts = datetime.strptime(prediction_date[:10], "%Y-%m-%d")
        t1_ts = datetime.strptime(maturity_date[:10], "%Y-%m-%d")
        t0_eligible = closes.index[closes.index <= t0_ts]
        t1_eligible = closes.index[closes.index <= t1_ts]
        if len(t0_eligible) < 1 or len(t1_eligible) < 1:
            continue
        p0 = float(closes.loc[t0_eligible[-1]])
        p1 = float(closes.loc[t1_eligible[-1]])
        if p0 <= 0:
            continue
        ret = (p1 - p0) / p0 * 100.0
        movers.append(
            {
                "symbol": sym,
                "weight_pct": round(weight * 100, 2),
                "return_horizon_pct": round(ret, 3),
                "index_contribution_pct": round(weight * ret, 4),
            }
        )
    movers.sort(key=lambda m: m["index_contribution_pct"])
    return movers[:limit] + list(reversed(movers[-limit:]))[:limit]


def categorize_miss(
    *,
    predicted_return_pct: float,
    actual_return_pct: float,
    macro_raw_pct: float | None = None,
    macro_delta_pct: float | None = None,
    factor_delta_horizon: list[dict[str, Any]],
    headlines_at_maturity: list[dict[str, str]],
    headlines_at_t0: list[dict[str, str]] | None = None,
    missing_factors_t0: list[str],
    missing_factors_t1: list[str],
    scope: str = "macro_only",
) -> str:
    critical_missing = [k for k in _CRITICAL_FACTORS if k in missing_factors_t0 or k in missing_factors_t1]
    if len(critical_missing) >= 2:
        return "factor_coverage_gap"

    if abs(predicted_return_pct) < _NEUTRAL_THRESHOLD_PCT:
        return "neutral_boundary"

    raw = macro_raw_pct if macro_raw_pct is not None else macro_delta_pct
    capped = macro_delta_pct if macro_delta_pct is not None else predicted_return_pct
    if raw is not None and abs(float(raw)) > _MACRO_DELTA_CAP_PCT + 0.01:
        if (float(raw) >= 0) != (float(predicted_return_pct) >= 0) or abs(float(raw) - float(capped)) > 0.5:
            pred_sign = predicted_return_pct >= 0
            actual_sign = actual_return_pct >= 0
            if pred_sign != actual_sign:
                return "cap_saturation"

    tags_t1: set[str] = set()
    for headline in headlines_at_maturity:
        tags_t1.update(_headline_tags(str(headline.get("title") or "")))
    tags_t0: set[str] = set()
    for headline in headlines_at_t0 or []:
        tags_t0.update(_headline_tags(str(headline.get("title") or "")))
    new_tags = tags_t1 - tags_t0
    if new_tags & _EVENT_TAGS:
        pred_sign = predicted_return_pct >= 0
        actual_sign = actual_return_pct >= 0
        if pred_sign != actual_sign:
            return "event_gap"

    if scope == "macro_only":
        pred_sign = predicted_return_pct >= 0
        actual_sign = actual_return_pct >= 0
        if pred_sign != actual_sign:
            supporting = 0
            for driver in factor_delta_horizon:
                factor = str(driver.get("factor") or "")
                if factor not in _REGIME_FACTORS:
                    continue
                delta = float(driver.get("delta") or 0)
                if actual_sign and delta > 0:
                    supporting += 1
                elif not actual_sign and delta < 0:
                    supporting += 1
            if supporting >= 2:
                return "regime_flip"
            return "missing_bottom_up"

    pred_sign = predicted_return_pct >= 0
    actual_sign = actual_return_pct >= 0
    return "regime_flip" if pred_sign != actual_sign else "unknown"


def build_learning_note(
    *,
    miss_category: str,
    predicted_return_pct: float,
    actual_return_pct: float,
    factor_delta_horizon: list[dict[str, Any]],
    macro_raw_pct: float | None = None,
) -> str:
    top = factor_delta_horizon[:3]
    top_txt = ", ".join(
        f"{d.get('label') or d.get('factor')} {float(d.get('delta') or 0):+.2f}"
        for d in top
    )
    if miss_category == "cap_saturation" and macro_raw_pct is not None:
        return (
            f"Ridge raw {macro_raw_pct:+.2f}% capped to {predicted_return_pct:+.2f}% but market moved "
            f"{actual_return_pct:+.2f}% — model magnitude saturated. Horizon factor drift: {top_txt or 'n/a'}."
        )
    if miss_category == "neutral_boundary":
        return (
            f"Forecast near flat ({predicted_return_pct:+.2f}%) vs actual {actual_return_pct:+.2f}% — "
            "sign-only scoring flagged a miss inside the neutral band."
        )
    if miss_category == "event_gap":
        return (
            f"Headlines at maturity suggest event drivers (war/oil/RBI/FII) not captured at prediction time; "
            f"actual {actual_return_pct:+.2f}% vs predicted {predicted_return_pct:+.2f}%."
        )
    if miss_category == "factor_coverage_gap":
        return "Missing macro factor values at T0 or maturity — backfill before trusting this eval row."
    if miss_category == "missing_bottom_up":
        return (
            f"Macro-only backtest missed direction ({predicted_return_pct:+.2f}% vs {actual_return_pct:+.2f}%). "
            f"Live hybrid (bottom-up + scenarios) may differ. Horizon drift: {top_txt or 'n/a'}."
        )
    return (
        f"Predicted {predicted_return_pct:+.2f}% vs actual {actual_return_pct:+.2f}%. "
        f"Largest horizon factor moves: {top_txt or 'n/a'}."
    )


def enrich_eval_row_horizon(
    eval_row: dict[str, Any],
    frame: pd.DataFrame,
    feature_cols: list[str],
    *,
    horizon_days: int,
    trading_dates: list[str],
) -> dict[str, Any]:
    """Attach T0/T1 snapshots and miss metadata to one backtest eval row."""
    pred_day = str(eval_row.get("date") or "")[:10]
    maturity = resolve_maturity_date(pred_day, horizon_days, trading_dates)
    out = dict(eval_row)
    out["maturity_date"] = maturity

    if not maturity:
        return out

    t0 = factor_snapshot_at(pred_day, frame, feature_cols, keys=MACRO_FACTOR_KEYS)
    t1 = factor_snapshot_at(maturity, frame, feature_cols, keys=MACRO_FACTOR_KEYS)
    deltas = compute_factor_delta_horizon(t0, t1)

    missing_t0 = [k for k in MACRO_FACTOR_KEYS if k not in t0]
    missing_t1 = [k for k in MACRO_FACTOR_KEYS if k not in t1]

    out["factor_snapshot_t0"] = {k: round(v, 4) for k, v in sorted(t0.items())[:12]}
    out["factor_snapshot_t1"] = {k: round(v, 4) for k, v in sorted(t1.items())[:12]}
    out["factor_delta_horizon"] = deltas
    out["horizon_price_path"] = horizon_price_path(frame, pred_day, maturity)

    predicted = float(eval_row.get("predicted_return_pct") or 0)
    actual = float(eval_row.get("actual_forward_return_pct") or 0)
    direction_correct = bool(eval_row.get("direction_correct"))

    headlines_t0 = _fetch_index_headlines(pred_day, limit=5)
    headlines = _fetch_index_headlines(maturity, limit=5)
    out["headlines_at_t0"] = headlines_t0
    out["headlines_at_maturity"] = headlines

    if not direction_correct:
        category = categorize_miss(
            predicted_return_pct=predicted,
            actual_return_pct=actual,
            macro_raw_pct=eval_row.get("macro_raw_pct"),
            macro_delta_pct=eval_row.get("macro_delta_pct"),
            factor_delta_horizon=deltas,
            headlines_at_maturity=headlines,
            headlines_at_t0=headlines_t0,
            missing_factors_t0=missing_t0,
            missing_factors_t1=missing_t1,
        )
        out["miss_category"] = category
        out["learning_note"] = build_learning_note(
            miss_category=category,
            predicted_return_pct=predicted,
            actual_return_pct=actual,
            factor_delta_horizon=deltas,
            macro_raw_pct=eval_row.get("macro_raw_pct"),
        )
        out["calendar_events_at_maturity"] = _calendar_events_for_date(date.fromisoformat(maturity))
        out["constituent_movers"] = _constituent_movers_horizon(pred_day, maturity)
        out["causal_hypotheses"] = build_causal_hypotheses(
            factor_drivers=[
                {
                    "factor": d["factor"],
                    "label": d["label"],
                    "prev": d["t0"],
                    "current": d["t1"],
                    "change_pct": d["change_pct"],
                }
                for d in deltas
            ],
            realized_1d_pct=actual,
            calendar_events=out.get("calendar_events_at_maturity") or [],
            index_headlines=headlines,
            constituent_headlines=collect_constituent_headlines_for_day(maturity, limit=4),
            move_threshold_pct=15.0,
        )

    return out


def analyze_prediction_miss(
    eval_row: dict[str, Any],
    *,
    frame: pd.DataFrame,
    feature_cols: list[str],
    horizon_days: int,
    trading_dates: list[str],
) -> dict[str, Any]:
    """Full miss analysis payload for one eval or ledger row."""
    enriched = enrich_eval_row_horizon(
        eval_row,
        frame,
        feature_cols,
        horizon_days=horizon_days,
        trading_dates=trading_dates,
    )
    predicted = float(eval_row.get("predicted_return_pct") or eval_row.get("expected_return_pct") or 0)
    actual = float(
        eval_row.get("actual_forward_return_pct") or eval_row.get("actual_return_pct") or 0
    )
    return {
        "prediction_date": str(eval_row.get("date") or eval_row.get("predicted_at", ""))[:10],
        "maturity_date": enriched.get("maturity_date"),
        "predicted_return_pct": predicted,
        "actual_return_pct": actual,
        "direction_correct": bool(eval_row.get("direction_correct")),
        "miss_category": enriched.get("miss_category"),
        "learning_note": enriched.get("learning_note"),
        "factor_delta_horizon": enriched.get("factor_delta_horizon") or [],
        "factor_snapshot_t0": enriched.get("factor_snapshot_t0") or {},
        "factor_snapshot_t1": enriched.get("factor_snapshot_t1") or {},
        "horizon_price_path": enriched.get("horizon_price_path") or [],
        "headlines_at_maturity": enriched.get("headlines_at_maturity") or [],
        "causal_hypotheses": enriched.get("causal_hypotheses") or [],
        "constituent_movers": enriched.get("constituent_movers") or [],
        "calendar_events_at_maturity": enriched.get("calendar_events_at_maturity") or [],
    }


def run_miss_analysis(
    *,
    days: int = 365,
    horizon_days: int = 14,
    ticker: str = "NIFTY",
    backtest_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze all backtest eval rows and summarize misses."""
    report = backtest_report
    if report is None:
        report = run_walk_forward_backtest(days=days, horizon_days=horizon_days)
    if report.get("status") != "ok":
        return {"status": "error", "message": report.get("message") or "backtest failed"}

    frame = load_aligned_factor_history(days=days)
    feature_cols = _feature_columns(frame)
    trading_dates = _trading_dates(frame)
    hz = int(report.get("horizon_days") or horizon_days)

    analyses: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []

    for row in report.get("daily_evaluations") or []:
        payload = analyze_prediction_miss(
            row,
            frame=frame,
            feature_cols=feature_cols,
            horizon_days=hz,
            trading_dates=trading_dates,
        )
        analyses.append(payload)
        if payload.get("direction_correct"):
            hits.append(payload)
        else:
            misses.append(payload)

    categories: dict[str, int] = {}
    for miss in misses:
        cat = str(miss.get("miss_category") or "unknown")
        categories[cat] = categories.get(cat, 0) + 1

    metrics = report.get("metrics") or {}
    capture_block: dict[str, Any] = {}
    try:
        from trade_integrations.hub_capture.channel import channel_stats_today
        from trade_integrations.hub_capture.registry import build_capture_stats
        from trade_integrations.hub_capture.rollup import capture_coverage_stats

        capture_block = {
            "stats": build_capture_stats(ticker.strip().upper()),
            "coverage": capture_coverage_stats(ticker.strip().upper()),
            "channel_today": channel_stats_today(),
        }
    except Exception:
        pass
    return {
        "status": "ok",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker.strip().upper(),
        "horizon_days": hz,
        "history_days": days,
        "eval_count": len(analyses),
        "summary": {
            "direction_hit_rate": metrics.get("direction_hit_rate"),
            "mae_pct": metrics.get("mae_pct"),
            "miss_count": len(misses),
            "hit_count": len(hits),
            "miss_categories": categories,
            "top_miss_patterns": _top_miss_patterns(misses),
            "capture_coverage": capture_block,
        },
        "misses": misses,
        "hits_sample": hits[:5],
        "all_analyses": analyses,
    }


def _top_miss_patterns(misses: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    if not misses:
        return []
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for miss in misses:
        cat = str(miss.get("miss_category") or "unknown")
        by_cat.setdefault(cat, []).append(miss)
    ranked = sorted(by_cat.items(), key=lambda kv: len(kv[1]), reverse=True)
    out: list[dict[str, Any]] = []
    for cat, rows in ranked[:limit]:
        out.append(
            {
                "category": cat,
                "count": len(rows),
                "example_dates": [r.get("prediction_date") for r in rows[:3]],
                "action": _action_for_category(cat),
            }
        )
    return out


def _action_for_category(category: str) -> str:
    actions = {
        "regime_flip": "Weight short-horizon momentum/FII more when VIX elevated; consider regime detector.",
        "cap_saturation": "Review macro delta cap — saturated bearish calls miss recoveries.",
        "neutral_boundary": "Use neutral band in direction scoring or widen flat threshold.",
        "event_gap": "Add explicit geopolitical/event features beyond oil proxy.",
        "factor_coverage_gap": "Run enrich_factor_history before evaluating misses.",
        "missing_bottom_up": "Enable hybrid backtest (bottom-up + scenarios) per Phase 6 quality plan.",
    }
    return actions.get(category, "Review factor contributors and scenario reconciliation.")


def run_and_save_miss_analysis(**kwargs: Any) -> dict[str, Any]:
    report = run_miss_analysis(**kwargs)
    if report.get("status") == "ok":
        save_miss_analysis_report(report, ticker=str(kwargs.get("ticker") or "NIFTY"))
    return report


def analyze_ledger_misses(
    ledger_rows: list[dict[str, Any]],
    *,
    days: int = 365,
    horizon_days: int = 14,
) -> list[dict[str, Any]]:
    """RCA for reconciled ledger rows that missed direction."""
    frame = load_aligned_factor_history(days=days)
    feature_cols = _feature_columns(frame)
    trading_dates = _trading_dates(frame)
    out: list[dict[str, Any]] = []
    for row in ledger_rows:
        if row.get("direction_correct") is not False:
            continue
        if row.get("actual_return_pct") is None:
            continue
        eval_like = {
            "date": str(row.get("predicted_at", ""))[:10],
            "predicted_return_pct": row.get("expected_return_pct"),
            "actual_forward_return_pct": row.get("actual_return_pct"),
            "direction_correct": False,
            "macro_delta_pct": (row.get("metadata") or {}).get("macro_delta_pct"),
            "macro_raw_pct": (row.get("metadata") or {}).get("macro_delta_pct"),
        }
        out.append(
            analyze_prediction_miss(
                eval_like,
                frame=frame,
                feature_cols=feature_cols,
                horizon_days=int(row.get("horizon_days") or horizon_days),
                trading_dates=trading_dates,
            )
        )
    return out
