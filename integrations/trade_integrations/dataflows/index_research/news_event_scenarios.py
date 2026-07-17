"""News event scenario drafts, quant runs, and hub persistence."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.cascade.calibration_store import (
    load_calibration_from_doc,
)
from trade_integrations.dataflows.index_research.factor_matrix import (
    MACRO_FACTOR_KEYS,
    NEWS_EVENT_MACRO_KEYS,
)
from trade_integrations.dataflows.index_research.pipeline_snapshot import (
    MissingSnapshotError,
    StaleSnapshotError,
    normalize_as_of,
    resolve_bound_pipeline_doc,
)
from trade_integrations.dataflows.index_research.simulate import (
    build_forecast_path,
    macro_factors_from_rows,
    simulate_index_prediction,
)

_INTENSITY_SCALE = {"low": 0.5, "medium": 1.0, "high": 1.5}
_ALLOWED_FACTORS = frozenset(MACRO_FACTOR_KEYS) | frozenset(NEWS_EVENT_MACRO_KEYS)


def scenarios_hub_dir(ticker: str = "NIFTY") -> Path:
    path = get_hub_dir() / ticker.strip().upper() / "news_event_scenarios"
    path.mkdir(parents=True, exist_ok=True)
    (path / "drafts").mkdir(exist_ok=True)
    (path / "history").mkdir(exist_ok=True)
    return path


def _parse_override_value(raw: Any, base: float, *, scale: float = 1.0) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("%"):
        try:
            pct = float(text[:-1].replace("+", "")) * scale
        except ValueError:
            return None
        return base * (1.0 + pct / 100.0)
    try:
        return float(text)
    except ValueError:
        return None


def parse_outcome_factor_overrides(
    outcome: dict[str, Any],
    macro_factors: dict[str, float],
    *,
    topic_tags: list[str] | None = None,
) -> tuple[dict[str, float], str | None, float | None]:
    """Return resolved overrides, primary_factor, primary_shock_pct for simulate."""
    intensity = str(outcome.get("intensity") or "medium").lower()
    scale = _INTENSITY_SCALE.get(intensity, 1.0)
    overrides: dict[str, float] = {}
    warnings: list[str] = []

    raw_overrides = outcome.get("factor_overrides") or {}
    if isinstance(raw_overrides, dict):
        for key, raw in raw_overrides.items():
            factor = str(key).strip()
            if factor not in _ALLOWED_FACTORS:
                warnings.append(f"dropped unknown factor {factor}")
                continue
            base = float(macro_factors.get(factor, 0.0) or 0.0)
            parsed = _parse_override_value(raw, base, scale=scale)
            if parsed is not None:
                overrides[factor] = parsed

    primary = str(outcome.get("primary_factor") or "").strip() or None
    primary_shock: float | None = None
    if primary and primary in _ALLOWED_FACTORS and primary not in overrides:
        topic_tags = []
        primary_shock = 5.0 * scale
        try:
            from trade_integrations.dataflows.index_research.news_shock_calibration import (
                calibrated_shock_pct_for_topic,
            )

            for tag in topic_tags or []:
                calibrated = calibrated_shock_pct_for_topic(str(tag))
                if calibrated:
                    primary_shock = abs(float(calibrated)) * scale
                    break
        except Exception:
            pass

    return overrides, primary if primary in _ALLOWED_FACTORS else None, primary_shock


def _trading_days_for_range(date_range: dict[str, Any] | None, default_horizon: int) -> int:
    if not date_range:
        return default_horizon
    start_raw = date_range.get("start")
    end_raw = date_range.get("end")
    if not start_raw or not end_raw:
        return default_horizon
    try:
        start = date.fromisoformat(str(start_raw)[:10])
        end = date.fromisoformat(str(end_raw)[:10])
    except ValueError:
        return default_horizon
    if end < start:
        return default_horizon
    calendar_days = (end - start).days + 1
    trading = max(1, int(calendar_days * 5 / 7))
    return max(1, min(default_horizon, trading))


def _path_with_calendar_dates(
    path_rows: list[dict[str, Any]],
    date_range: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not path_rows or not date_range:
        return path_rows
    start_raw = date_range.get("start")
    end_raw = date_range.get("end")
    if not start_raw or not end_raw:
        return path_rows
    try:
        start = date.fromisoformat(str(start_raw)[:10])
        end = date.fromisoformat(str(end_raw)[:10])
    except ValueError:
        return path_rows
    n = len(path_rows)
    if n <= 1:
        return [{**path_rows[0], "date": start.isoformat()}]
    out: list[dict[str, Any]] = []
    span = (end - start).days
    for i, row in enumerate(path_rows):
        t = i / max(n - 1, 1)
        day = start if span <= 0 else start.fromordinal(start.toordinal() + int(round(span * t)))
        out.append({**row, "date": day.isoformat()})
    return out


def save_news_scenario_draft(
    *,
    ticker: str,
    pipeline_as_of: str,
    draft: dict[str, Any],
) -> dict[str, Any]:
    """Persist or update a scenario draft."""
    resolve_bound_pipeline_doc(ticker, pipeline_as_of)
    draft_id = str(draft.get("draft_id") or uuid.uuid4().hex[:16])
    payload = {
        **draft,
        "draft_id": draft_id,
        "ticker": ticker.strip().upper(),
        "pipeline_as_of": normalize_as_of(pipeline_as_of),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    hub = scenarios_hub_dir(ticker)
    path = hub / "drafts" / f"{draft_id}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


def load_news_scenario_draft(ticker: str, draft_id: str) -> dict[str, Any] | None:
    path = scenarios_hub_dir(ticker) / "drafts" / f"{draft_id}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def run_news_event_scenario(
    *,
    ticker: str,
    pipeline_as_of: str,
    draft_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Quant run for all outcomes in a draft; persist product artifact."""
    doc, _model = resolve_bound_pipeline_doc(ticker, pipeline_as_of)
    draft = load_news_scenario_draft(ticker, draft_id)
    if draft is None:
        raise MissingSnapshotError(f"Draft {draft_id} not found")

    macro = macro_factors_from_rows(doc.global_factors or [])
    pred = doc.prediction or {}
    bottom_up = float(pred.get("bottom_up_return_pct") or 0.0)
    headline = float(pred.get("expected_return_pct") or 0.0)
    horizon_default = int((doc.horizon or {}).get("days") or 14)
    date_range = draft.get("date_range") if isinstance(draft.get("date_range"), dict) else None
    simulate_horizon = _trading_days_for_range(date_range, horizon_default)

    india_vix = macro.get("india_vix")
    if india_vix is None and isinstance(doc.regime, dict):
        india_vix = doc.regime.get("india_vix")
    calibration = load_calibration_from_doc(doc)

    baseline_sim = simulate_index_prediction(
        macro_factors=macro,
        spot=float(doc.spot or 0),
        bottom_up_return_pct=bottom_up,
        horizon_days=simulate_horizon,
        headline_return_pct=headline,
        event_impact_curves=doc.event_impact_curves,
        cascade_calibration=calibration,
        india_vix=india_vix,
    )

    baseline_return = float(baseline_sim.get("baseline_return_pct") or headline)
    baseline_path = _path_with_calendar_dates(
        [
            {
                "day": row.get("day"),
                "spot": row.get("baseline_level"),
                "return_pct": row.get("baseline_return_pct"),
            }
            for row in baseline_sim.get("forecast_path") or []
        ],
        date_range,
    )

    range_block = baseline_sim.get("range") if isinstance(baseline_sim.get("range"), dict) else {}
    baseline_block = {
        "spot": doc.spot,
        "expected_return_pct": baseline_return,
        "bottom_up_return_pct": bottom_up,
        "macro_delta_pct": baseline_sim.get("macro_delta_pct"),
        "range": range_block,
        "path": baseline_path,
    }

    outcome_rows: list[dict[str, Any]] = []
    low_spots: list[float] = []
    high_spots: list[float] = []

    event = draft.get("event") if isinstance(draft.get("event"), dict) else {}
    topic_tags = list(event.get("topic_tags") or [])

    for outcome in draft.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        overrides, primary_factor, primary_shock = parse_outcome_factor_overrides(
            outcome, macro, topic_tags=topic_tags
        )
        sim = simulate_index_prediction(
            macro_factors=macro,
            factor_overrides=overrides or None,
            spot=float(doc.spot or 0),
            bottom_up_return_pct=bottom_up,
            horizon_days=simulate_horizon,
            headline_return_pct=headline,
            primary_factor=primary_factor,
            primary_shock_pct=primary_shock,
            event_preset_id=outcome.get("event_preset_id"),
            event_impact_curves=doc.event_impact_curves,
            cascade_calibration=calibration,
            india_vix=india_vix,
        )
        total_return = float(sim.get("expected_return_pct") or 0.0)
        path = _path_with_calendar_dates(
            [
                {
                    "day": row.get("day"),
                    "spot": row.get("scenario_level"),
                    "return_pct": row.get("scenario_return_pct"),
                }
                for row in sim.get("forecast_path") or []
            ],
            date_range,
        )
        sim_range = sim.get("range") if isinstance(sim.get("range"), dict) else {}
        if sim_range.get("low") is not None:
            low_spots.append(float(sim_range["low"]))
        if sim_range.get("high") is not None:
            high_spots.append(float(sim_range["high"]))
        contributors = (sim.get("factor_explanation") or {}).get("contributors") or []
        outcome_rows.append(
            {
                "id": outcome.get("id") or uuid.uuid4().hex[:8],
                "label": outcome.get("label"),
                "intensity": outcome.get("intensity"),
                "probability_hint": outcome.get("probability_hint"),
                "expected_return_pct": total_return,
                "macro_delta_pct": sim.get("macro_delta_pct"),
                "bottom_up_return_pct": bottom_up,
                "range": sim_range,
                "path": path,
                "contributors": contributors[:8],
                "factor_overrides_applied": sim.get("factor_overrides") or overrides,
            }
        )

    scenario_id = uuid.uuid4().hex[:16]
    product = {
        "scenario_id": scenario_id,
        "draft_id": draft_id,
        "session_id": session_id,
        "pipeline_as_of": normalize_as_of(pipeline_as_of),
        "ticker": ticker.strip().upper(),
        "date_range": date_range,
        "event": draft.get("event") or {},
        "baseline": baseline_block,
        "outcomes": outcome_rows,
        "fan_band": {
            "low": min(low_spots) if low_spots else None,
            "high": max(high_spots) if high_spots else None,
        },
        "simulate_horizon_days": simulate_horizon,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    hub = scenarios_hub_dir(ticker)
    history_path = hub / "history" / f"{scenario_id}.json"
    history_path.write_text(json.dumps(product, indent=2, default=str), encoding="utf-8")
    (hub / "latest.json").write_text(json.dumps(product, indent=2, default=str), encoding="utf-8")
    return product


def load_news_event_scenario(ticker: str, scenario_id: str) -> dict[str, Any] | None:
    path = scenarios_hub_dir(ticker) / "history" / f"{scenario_id}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_recent_news_scenarios(ticker: str, limit: int = 10) -> list[dict[str, Any]]:
    hub = scenarios_hub_dir(ticker) / "history"
    if not hub.is_dir():
        return []
    files = sorted(hub.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for path in files[:limit]:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            out.append(
                {
                    "scenario_id": row.get("scenario_id"),
                    "created_at": row.get("created_at"),
                    "event": row.get("event"),
                    "outcome_count": len(row.get("outcomes") or []),
                }
            )
        except (json.JSONDecodeError, OSError):
            continue
    return out
