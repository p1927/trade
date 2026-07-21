"""T0-safe daily news event features from verified hub records."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.factor_store import upsert_daily_factors
from trade_integrations.dataflows.index_research.news_tags import tags_from_dict

_LOOKBACK_DAYS = 7
_PRIOR_WINDOW_DAYS = 7

NEWS_EVENT_FACTOR_KEYS: tuple[str, ...] = (
    "news_material_7d",
    "news_war_7d",
    "news_oil_7d",
    "news_fii_7d",
    "news_rbi_7d",
    "news_crash_theme_7d",
    "news_rally_theme_7d",
    "news_net_tone_7d",
    "news_surprise_7d",
)

_TOPIC_FEATURE_MAP: dict[str, str] = {
    "war": "news_war_7d",
    "oil": "news_oil_7d",
    "fii": "news_fii_7d",
    "rbi": "news_rbi_7d",
}

_CRASH_THEMES = frozenset({"crash", "selloff"})
_RALLY_THEMES = frozenset({"rally", "recovery"})

_COVERAGE_MIN_PCT = 60.0
_RECONCILED_MIN_COUNT = 30


def _config_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "news_model_config.json"


def _coverage_path(ticker: str = "NIFTY") -> Path:
    return get_hub_dir() / ticker.strip().upper() / "index_research" / "news_feature_coverage.json"


def load_news_model_config(ticker: str = "NIFTY") -> dict[str, Any]:
    path = _config_path(ticker)
    if not path.is_file():
        return {
            "news_event_features": "pending",
            "news_event_overlay": "pending",
            "updated_at": None,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"news_event_features": "pending", "news_event_overlay": "pending"}
    return payload if isinstance(payload, dict) else {"news_event_features": "pending"}


def save_news_model_config(config: dict[str, Any], *, ticker: str = "NIFTY") -> Path:
    path = _config_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**config, "updated_at": datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def is_news_ridge_enabled(ticker: str = "NIFTY") -> bool:
    status = str(load_news_model_config(ticker).get("news_event_features") or "pending")
    return status in {"pending", "accepted"}


def is_news_overlay_enabled(ticker: str = "NIFTY") -> bool:
    """Overlay applies only after shock calibration passes promotion gates."""
    status = str(load_news_model_config(ticker).get("news_event_overlay") or "pending")
    return status == "accepted"


def _parse_day(raw: str) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _day_window(end_day: str, *, lookback: int) -> list[str]:
    end = _parse_day(end_day)
    if end is None:
        return []
    return [(end - timedelta(days=offset)).isoformat() for offset in range(lookback, -1, -1)]


def _load_hub_events(*, ticker: str = "NIFTY") -> list[dict[str, Any]]:
    from trade_integrations.hub_storage.verified_news_store import list_verified_records

    return list_verified_records(limit=10000, ticker=ticker, include_rejected=False)


def _index_records_by_day(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_day: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        tags = tags_from_dict(record.get("tags"))
        day = (tags.publish_day or str(record.get("published_at") or ""))[:10]
        if len(day) < 10:
            continue
        by_day.setdefault(day, []).append(record)
    return by_day


def _topics_for_record(record: dict[str, Any]) -> set[str]:
    tags = tags_from_dict(record.get("tags"))
    return set(tags.topics)


def _themes_for_record(record: dict[str, Any]) -> set[str]:
    tags = tags_from_dict(record.get("tags"))
    return set(tags.themes)


def compute_news_features_for_day(
    day: str,
    *,
    by_day: dict[str, list[dict[str, Any]]] | None = None,
    ticker: str = "NIFTY",
) -> dict[str, float]:
    """T0-safe rolling news intensities for a single calendar day."""
    if by_day is None:
        by_day = _index_records_by_day(_load_hub_events(ticker=ticker))

    window_days = _day_window(day, lookback=_LOOKBACK_DAYS)
    prior_days = _day_window(
        (date.fromisoformat(day[:10]) - timedelta(days=_PRIOR_WINDOW_DAYS)).isoformat(),
        lookback=_PRIOR_WINDOW_DAYS,
    )

    material = 0
    topic_counts: dict[str, int] = {k: 0 for k in _TOPIC_FEATURE_MAP}
    crash_count = 0
    rally_count = 0
    topics_in_window: set[str] = set()
    topics_in_prior: set[str] = set()

    for wday in window_days:
        for record in by_day.get(wday, []):
            material += 1
            topics = _topics_for_record(record)
            themes = _themes_for_record(record)
            topics_in_window.update(topics)
            for topic, feature in _TOPIC_FEATURE_MAP.items():
                if topic in topics:
                    topic_counts[topic] += 1
            if themes & _CRASH_THEMES:
                crash_count += 1
            if themes & _RALLY_THEMES:
                rally_count += 1

    for pday in prior_days:
        for record in by_day.get(pday, []):
            topics_in_prior.update(_topics_for_record(record))

    surprise = len(topics_in_window - topics_in_prior)
    tone_denom = max(material, 1)
    net_tone = (rally_count - crash_count) / tone_denom

    return {
        "news_material_7d": float(material),
        "news_war_7d": float(topic_counts["war"]),
        "news_oil_7d": float(topic_counts["oil"]),
        "news_fii_7d": float(topic_counts["fii"]),
        "news_rbi_7d": float(topic_counts["rbi"]),
        "news_crash_theme_7d": float(crash_count),
        "news_rally_theme_7d": float(rally_count),
        "news_net_tone_7d": round(net_tone, 4),
        "news_surprise_7d": float(surprise),
    }


def news_features_to_factor_rows(features: dict[str, float]) -> list[dict[str, Any]]:
    return [
        {"factor": key, "value": float(features.get(key) or 0.0), "source": "news_hub_verified"}
        for key in NEWS_EVENT_FACTOR_KEYS
    ]


def backfill_news_event_features(
    *,
    trading_dates: list[str] | None = None,
    ticker: str = "NIFTY",
) -> dict[str, Any]:
    """Persist news_* factors for each trading day in the aligned history window."""
    if trading_dates is None:
        from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history

        nifty = load_nifty_history(days=365)
        if nifty.empty:
            return {"status": "error", "message": "no nifty history", "days_written": 0}
        trading_dates = nifty["date"].astype(str).str[:10].tolist()

    records = _load_hub_events(ticker=ticker)
    by_day = _index_records_by_day(records)
    days_written = 0
    for day in trading_dates:
        features = compute_news_features_for_day(day, by_day=by_day)
        upsert_daily_factors(day, news_features_to_factor_rows(features))
        days_written += 1

    coverage = audit_news_feature_coverage(trading_dates=trading_dates, by_day=by_day, ticker=ticker)
    return {
        "status": "ok",
        "days_written": days_written,
        "hub_events": len(records),
        "coverage": coverage,
    }


def audit_news_feature_coverage(
    *,
    trading_dates: list[str] | None = None,
    by_day: dict[str, list[dict[str, Any]]] | None = None,
    ticker: str = "NIFTY",
    save: bool = True,
) -> dict[str, Any]:
    """Audit % of trading days with material headlines in 7d lookback."""
    if trading_dates is None:
        from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history

        nifty = load_nifty_history(days=365)
        trading_dates = nifty["date"].astype(str).str[:10].tolist() if not nifty.empty else []

    if by_day is None:
        by_day = _index_records_by_day(_load_hub_events(ticker=ticker))

    total = len(trading_dates)
    with_material = 0
    for day in trading_dates:
        feats = compute_news_features_for_day(day, by_day=by_day)
        if feats.get("news_material_7d", 0) > 0:
            with_material += 1

    coverage_pct = round(with_material / total * 100, 2) if total else 0.0

    from trade_integrations.hub_storage.verified_news_store import list_verified_records

    reconciled = 0
    for rec in list_verified_records(limit=10000, ticker=ticker, include_rejected=False):
        actual = rec.get("actual_impact") or rec.get("actual") or {}
        if actual.get("return_pct") is not None:
            reconciled += 1

    report = {
        "status": "ok",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
        "trading_days": total,
        "days_with_material_7d": with_material,
        "coverage_pct": coverage_pct,
        "reconciled_stories": reconciled,
        "gates": {
            "coverage_min_pct": _COVERAGE_MIN_PCT,
            "coverage_met": coverage_pct >= _COVERAGE_MIN_PCT,
            "reconciled_min_count": _RECONCILED_MIN_COUNT,
            "reconciled_met": reconciled >= _RECONCILED_MIN_COUNT,
            "ridge_ablation_ready": coverage_pct >= _COVERAGE_MIN_PCT,
            "overlay_ready": reconciled >= _RECONCILED_MIN_COUNT,
        },
    }
    if save:
        path = _coverage_path(ticker)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def load_news_feature_coverage(ticker: str = "NIFTY") -> dict[str, Any] | None:
    path = _coverage_path(ticker)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def evaluate_news_model_gates(*, ticker: str = "NIFTY", gate_pp: float = 3.0) -> dict[str, Any]:
    """Run OOS promotion for Ridge news block and overlay readiness; persist news_model_config."""
    from trade_integrations.dataflows.index_research.equation_diagnostics import (
        _DEFAULT_EVAL_STEP,
        _MIN_TRAIN_ROWS,
        _walk_forward_hit_rate,
        _walk_forward_hit_rate_with_force_keys,
    )
    from trade_integrations.dataflows.index_research.sources.history_loader import load_aligned_factor_history

    coverage = audit_news_feature_coverage(ticker=ticker, save=True)
    gates = coverage.get("gates") or {}
    frame = load_aligned_factor_history(days=365)
    present = [k for k in NEWS_EVENT_FACTOR_KEYS if k in frame.columns]

    ridge_status = "pending"
    ridge_delta_pp = None
    baseline_hit = None
    with_news_hit = None
    if present and gates.get("ridge_ablation_ready"):
        baseline_hit = _walk_forward_hit_rate(
            frame,
            horizon_days=14,
            min_train_rows=_MIN_TRAIN_ROWS,
            eval_step=_DEFAULT_EVAL_STEP,
        )
        with_news_hit = _walk_forward_hit_rate_with_force_keys(
            frame,
            force_keys=tuple(present),
            horizon_days=14,
            min_train_rows=_MIN_TRAIN_ROWS,
            eval_step=_DEFAULT_EVAL_STEP,
        )
        if baseline_hit is not None and with_news_hit is not None:
            ridge_delta_pp = round((with_news_hit - baseline_hit) * 100, 2)
            ridge_status = "accepted" if ridge_delta_pp >= gate_pp else "rejected"

    overlay_status = "accepted" if gates.get("overlay_ready") else "pending"
    if not gates.get("overlay_ready") and int(coverage.get("reconciled_stories") or 0) == 0:
        overlay_status = "pending"

    config = {
        "news_event_features": ridge_status,
        "news_event_overlay": overlay_status,
        "ridge_ablation": {
            "baseline_hit_rate": baseline_hit,
            "with_news_hit_rate": with_news_hit,
            "delta_pp": ridge_delta_pp,
            "gate_pp": gate_pp,
            "keys_present": present,
        },
        "coverage": coverage,
    }
    save_news_model_config(config, ticker=ticker)
    return config
