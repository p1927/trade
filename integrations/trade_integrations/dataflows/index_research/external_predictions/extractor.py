"""LLM structured extraction for third-party NIFTY forecasts."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSource,
    ExternalPredictionTarget,
    utc_now_iso,
)
from trade_integrations.dataflows.index_research.external_predictions.validators import (
    validate_record,
)
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger

logger = logging.getLogger(__name__)

_MAX_PARSE_RETRIES = 2

_JSON_OBJECT = re.compile(r"\{[\s\S]*\}")
_LEVEL_PATTERN = re.compile(
    r"nifty(?:\s*50)?[^0-9]{0,40}(\d{1,2}[,.]?\d{3,5}(?:\.\d+)?)",
    re.I,
)
_RANGE_PATTERN = re.compile(
    r"(\d{1,2}[,.]?\d{3,5})\s*(?:-|to|–)\s*(\d{1,2}[,.]?\d{3,5})",
    re.I,
)
_TABLE_LEVEL = re.compile(r"\d{1,2}[,.]?\d{3,5}(?:\.\d+)?")
_NEXT_WEEK_SECTION = re.compile(
    r"next\s+week[^#\n]{0,160}nifty\s*50[^#\n]{0,160}(?:prediction|forecast)",
    re.I,
)
_NEXT_MONTH_SECTION = re.compile(
    r"next\s+month[^#\n]{0,160}nifty[^#\n]{0,160}(?:prediction|forecast)",
    re.I,
)
_DATE_ROW = re.compile(r"\b\d{2}-\d{2}-\d{4}\b")

_HORIZON_TABLE_BOOST = {
    "next_week_table": 5.0,
    "next_month_table": 4.0,
}


def markdown_horizon_table_signal(body: str, *, horizon_days: int) -> str:
    """Return next_week_table|next_month_table when a dated horizon section exists."""
    hz = max(1, int(horizon_days))
    if hz <= 14:
        section = _section_after_heading(body or "", _NEXT_WEEK_SECTION)
        if section.strip() and _DATE_ROW.search(section):
            return "next_week_table"
    else:
        section = _section_after_heading(body or "", _NEXT_MONTH_SECTION)
        if section.strip() and _DATE_ROW.search(section):
            return "next_month_table"
    return ""


def horizon_forecast_provenance(
    body: str,
    *,
    horizon_days: int,
    regex_style: str = "",
) -> dict[str, Any]:
    """Structured provenance for hub/table forecasts (section + date window)."""
    meta: dict[str, Any] = {}
    style = regex_style or markdown_horizon_table_signal(body, horizon_days=horizon_days)
    if style:
        meta["forecast_section"] = style
    hz = max(1, int(horizon_days))
    heading_match = _NEXT_WEEK_SECTION if hz <= 14 else _NEXT_MONTH_SECTION
    match = heading_match.search(body or "")
    if match:
        heading = match.group(0).strip()
        tail = (body or "")[match.end() : match.end() + 120]
        date_blob = f"{heading} {tail}"
        if heading:
            meta["forecast_heading"] = heading[:200]
        dates = re.findall(r"\d{2}-\d{2}-\d{4}", date_blob)
        if len(dates) >= 2:
            meta["horizon_window"] = f"{dates[0]} to {dates[-1]}"
        elif dates:
            meta["horizon_window"] = dates[0]
    return meta


def crawl_result_horizon_boost(markdown: str, *, horizon_days: int) -> float:
    """Content score boost when markdown contains a horizon-matching forecast table."""
    style = markdown_horizon_table_signal(markdown, horizon_days=horizon_days)
    return _HORIZON_TABLE_BOOST.get(style, 0.0)


def _ensure_env_loaded() -> None:
    try:
        from trade_integrations.env import load_trade_env

        load_trade_env()
    except Exception:
        logger.debug("load_trade_env skipped", exc_info=True)


def _parse_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    candidates: list[str] = []
    match = _JSON_OBJECT.search(text)
    if match:
        candidates.append(match.group(0))
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for blob in candidates:
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not (out == out):
        return None
    return out


def _normalize_level(value: float | None, spot: float | None) -> float | None:
    if value is None:
        return None
    if spot and spot > 1000 and value < 500:
        return None
    if value < 5000 or value > 50000:
        return None
    if spot and spot > 0:
        change = abs(value - spot) / spot
        if change > 0.5:
            return None
    return round(value, 2)


def _horizon_label(horizon_days: int) -> str:
    if horizon_days <= 7:
        return "short-term (~1 week)"
    if horizon_days <= 14:
        return "near-term (~2 weeks)"
    if horizon_days <= 30:
        return "medium-term (~1 month)"
    return f"~{horizon_days} trading days"


def _build_prompt(
    *,
    source: ExternalPredictionSource,
    horizon_days: int,
    spot: float | None,
    title: str,
    url: str,
    published_at: str,
    body: str,
    validator_errors: list[str] | None = None,
) -> str:
    spot_line = f"Current NIFTY spot reference: {spot:.0f}" if spot else "Current NIFTY spot: unknown"
    retry_block = ""
    if validator_errors:
        retry_block = (
            "\nPrevious extraction failed validation:\n- "
            + "\n- ".join(validator_errors)
            + "\nFix these issues in your JSON output.\n"
        )
    return f"""Extract a structured NIFTY 50 index forecast from the article below.

Source: {source.display_name}
User-selected horizon: {horizon_days} trading days ({_horizon_label(horizon_days)})
{spot_line}
Published: {published_at or "unknown"}
URL: {url}
Title: {title}
{retry_block}
Return ONLY valid JSON with this schema:
{{
  "has_prediction": true,
  "instrument": "NIFTY50",
  "target_low": number or null,
  "target_mid": number or null,
  "target_high": number or null,
  "target_date": "YYYY-MM-DD or empty",
  "direction": "bullish" | "bearish" | "neutral",
  "expected_return_pct": number or null,
  "summary": "2-3 sentence overview of their NIFTY view and timing",
  "rationale_bullets": ["driver 1", "driver 2", "driver 3"],
  "confidence": "high" | "medium" | "low",
  "published_at": "YYYY-MM-DD or empty"
}}

Rules:
- ONLY extract explicit NIFTY 50 / Nifty index LEVEL targets (index points 15000-35000).
- REJECT single-stock price targets, Sensex-only targets, options strategies, F&O commentary.
- REJECT if the page is a stock listicle without a clear NIFTY 50 index level forecast.
- Prefer forecasts whose target_date is ~{horizon_days} trading days from today ({_horizon_label(horizon_days)}).
- If no explicit NIFTY 50 index target is stated, set has_prediction=false and null targets.
- summary: plain-language context a trader can verify against the article.
- rationale_bullets: 3-5 bullets explaining WHY they expect that move (flows, events, earnings, macro, technicals).

Article:
{body[:8000]}
"""


def _build_rationale_prompt(
    *,
    source: ExternalPredictionSource,
    horizon_days: int,
    title: str,
    url: str,
    body: str,
    target_mid: float | None,
) -> str:
    target_line = f"Known NIFTY target from article: {target_mid:,.0f}" if target_mid else "Target level mentioned in article"
    return f"""Summarize the reasoning behind this NIFTY 50 forecast from the article below.

Source: {source.display_name}
Horizon context: {horizon_days} trading days ({_horizon_label(horizon_days)})
{target_line}
URL: {url}
Title: {title}

Return ONLY valid JSON:
{{
  "summary": "2-3 sentence overview",
  "rationale_bullets": ["reason 1", "reason 2", "reason 3"]
}}

Article:
{body[:6000]}
"""


def _section_after_heading(body: str, heading: re.Pattern[str]) -> str:
    match = heading.search(body or "")
    if not match:
        return ""
    rest = body[match.end() :]
    next_heading = re.search(r"\n##\s", rest)
    return rest[: next_heading.start()] if next_heading else rest


def _levels_from_dated_table(section: str, spot: float | None) -> ExternalPredictionTarget:
    """Parse daily support/resistance (or support/resistance/trend) rows in a forecast table."""
    target = ExternalPredictionTarget()
    supports: list[float] = []
    resistances: list[float] = []
    trends: list[float] = []
    for line in section.splitlines():
        if not _DATE_ROW.search(line):
            continue
        nums = [
            _normalize_level(_maybe_float(raw), spot)
            for raw in _TABLE_LEVEL.findall(line)
        ]
        nums = [n for n in nums if n is not None]
        if len(nums) >= 3:
            supports.append(nums[0])
            resistances.append(nums[1])
            trends.append(nums[2])
        elif len(nums) >= 2:
            supports.append(nums[0])
            resistances.append(nums[-1])
    if not supports and not resistances:
        return target
    if supports:
        target.low = min(supports)
    if resistances:
        target.high = max(resistances)
    if trends:
        target.mid = round(sum(trends) / len(trends), 2)
    elif target.low is not None and target.high is not None:
        target.mid = round((target.low + target.high) / 2, 2)
    elif resistances:
        target.mid = round(sum(resistances) / len(resistances), 2)
    return target


def _horizon_table_fallback(
    body: str,
    spot: float | None,
    *,
    horizon_days: int,
) -> tuple[ExternalPredictionTarget, str]:
    """Prefer Next Week / Next Month tables for structured forecast hub pages."""
    hz = max(1, int(horizon_days))
    if hz <= 14:
        section = _section_after_heading(body, _NEXT_WEEK_SECTION)
        style = "next_week_table"
    else:
        section = _section_after_heading(body, _NEXT_MONTH_SECTION)
        style = "next_month_table"
    if not section.strip():
        return ExternalPredictionTarget(), ""
    target = _levels_from_dated_table(section, spot)
    if any(v is not None for v in (target.low, target.mid, target.high)):
        return target, style
    return ExternalPredictionTarget(), ""


def _regex_fallback(
    body: str,
    spot: float | None,
    *,
    horizon_days: int = 14,
) -> tuple[ExternalPredictionTarget, str]:
    target = ExternalPredictionTarget()
    table_target, table_style = _horizon_table_fallback(body, spot, horizon_days=horizon_days)
    if any(v is not None for v in (table_target.low, table_target.mid, table_target.high)):
        return table_target, table_style
    range_match = _RANGE_PATTERN.search(body)
    if range_match:
        low = _normalize_level(_maybe_float(range_match.group(1)), spot)
        high = _normalize_level(_maybe_float(range_match.group(2)), spot)
        if low and high:
            target.low = min(low, high)
            target.high = max(low, high)
            target.mid = round((target.low + target.high) / 2, 2)
            return target, "range_pattern"
    levels: list[float] = []
    for match in _LEVEL_PATTERN.finditer(body):
        level = _normalize_level(_maybe_float(match.group(1)), spot)
        if level:
            levels.append(level)
    if not levels:
        return target, ""
    if len(levels) >= 2:
        target.low = min(levels)
        target.high = max(levels)
        target.mid = round(sum(levels) / len(levels), 2)
    else:
        target.mid = levels[0]
    return target, "level_pattern"


def _call_minimax(prompt: str, *, max_tokens: int = 1200) -> dict[str, Any]:
    from trade_integrations.dataflows.index_research.news_distillation import (
        call_minimax_json_text,
    )

    raw = call_minimax_json_text(prompt, max_tokens=max_tokens)
    return _parse_json_object(raw)


def extract_prediction_from_text(
    *,
    source: ExternalPredictionSource,
    horizon_days: int,
    spot: float | None,
    title: str,
    url: str,
    snippet: str,
    body: str,
    published_at: str = "",
    symbol: str = "NIFTY",
    pipeline: PipelineLogger | None = None,
) -> ExternalPredictionRecord:
    """Extract structured prediction via LLM with regex fallback."""
    _ensure_env_loaded()
    text = body or snippet
    record = ExternalPredictionRecord(
        source_id=source.id,
        symbol=symbol.upper(),
        horizon_days=horizon_days,
        as_of=date.today().isoformat(),
        spot_at_fetch=spot,
        provenance={"url": url, "title": title, "snippet": snippet[:500]},
        fetch_status="not_found",
    )

    if not text.strip():
        record.error_message = "No article text available"
        return record

    validator_errors: list[str] = []
    last_record = record

    for attempt in range(_MAX_PARSE_RETRIES + 1):
        payload: dict[str, Any] = {}
        model_name = "regex"
        try:
            prompt = _build_prompt(
                source=source,
                horizon_days=horizon_days,
                spot=spot,
                title=title,
                url=url,
                published_at=published_at,
                body=text,
                validator_errors=validator_errors or None,
            )
            payload = _call_minimax(prompt, max_tokens=1400)
            model_name = "minimax"
            if pipeline:
                pipeline.info(
                    "extract",
                    "MiniMax extraction completed",
                    source_id=source.id,
                    attempt=attempt + 1,
                )
        except Exception as exc:
            if pipeline:
                pipeline.warn(
                    "extract",
                    f"MiniMax unavailable — using regex fallback: {exc}",
                    source_id=source.id,
                )
            logger.debug("LLM extraction failed for %s: %s", source.id, exc)

        has_prediction = bool(payload.get("has_prediction", True))
        target = ExternalPredictionTarget(
            low=_normalize_level(_maybe_float(payload.get("target_low")), spot),
            mid=_normalize_level(_maybe_float(payload.get("target_mid")), spot),
            high=_normalize_level(_maybe_float(payload.get("target_high")), spot),
        )
        used_regex_target = False
        regex_style = ""
        if not any(v is not None for v in (target.low, target.mid, target.high)):
            target, regex_style = _regex_fallback(text, spot, horizon_days=horizon_days)
            used_regex_target = any(v is not None for v in (target.low, target.mid, target.high))
            if used_regex_target:
                model_name = "regex"
                if pipeline:
                    pipeline.info("extract", "Regex fallback found numeric target", source_id=source.id)

        if not has_prediction and not any(
            v is not None for v in (target.low, target.mid, target.high)
        ):
            record.error_message = "No NIFTY target found in source"
            return record

        if not any(v is not None for v in (target.low, target.mid, target.high)):
            record.error_message = "No numeric NIFTY target extracted"
            return record

        direction = str(payload.get("direction") or "neutral")
        if direction not in {"bullish", "bearish", "neutral"}:
            if spot and target.mid:
                direction = "bullish" if target.mid >= spot else "bearish"
            else:
                direction = "neutral"

        summary = str(payload.get("summary") or "").strip()
        bullets = payload.get("rationale_bullets") or []
        if not isinstance(bullets, list):
            bullets = []
        bullets = [str(b).strip() for b in bullets if str(b).strip()]

        if (not summary or len(bullets) < 2) and model_name == "regex" and used_regex_target:
            try:
                rationale_payload = _call_minimax(
                    _build_rationale_prompt(
                        source=source,
                        horizon_days=horizon_days,
                        title=title,
                        url=url,
                        body=text,
                        target_mid=target.mid,
                    ),
                    max_tokens=900,
                )
                if not summary:
                    summary = str(rationale_payload.get("summary") or "").strip()
                extra = rationale_payload.get("rationale_bullets") or []
                if isinstance(extra, list):
                    for item in extra:
                        line = str(item).strip()
                        if line and line not in bullets:
                            bullets.append(line)
                if bullets:
                    model_name = "minimax+rationale"
                    if pipeline:
                        pipeline.info("extract", "MiniMax rationale pass completed", source_id=source.id)
            except Exception as exc:
                if pipeline:
                    pipeline.warn("extract", f"Rationale LLM pass skipped: {exc}", source_id=source.id)

        if not bullets:
            if summary:
                bullets = [summary]
            elif snippet:
                bullets = [snippet[:240]]

        confidence = str(payload.get("confidence") or "medium")
        if confidence not in {"high", "medium", "low"}:
            confidence = "high" if "minimax" in model_name and body else "low"

        expected_return = _maybe_float(payload.get("expected_return_pct"))
        if expected_return is None and spot and target.mid:
            expected_return = round((target.mid - spot) / spot * 100, 2)

        pub = str(payload.get("published_at") or published_at or "")[:10]

        attempt_record = ExternalPredictionRecord(
            source_id=source.id,
            symbol=symbol.upper(),
            horizon_days=horizon_days,
            as_of=date.today().isoformat(),
            spot_at_fetch=spot,
            target=target,
            target_date=str(payload.get("target_date") or "")[:10],
            direction=direction,  # type: ignore[arg-type]
            expected_return_pct=expected_return,
            rationale_bullets=bullets[:6],
            confidence=confidence,  # type: ignore[arg-type]
            published_at=pub,
            provenance={
                "url": url,
                "title": title,
                "snippet": snippet[:500],
                "summary": summary,
                "horizon_days": horizon_days,
                "instrument": str(payload.get("instrument") or "NIFTY50"),
                **horizon_forecast_provenance(
                    text,
                    horizon_days=horizon_days,
                    regex_style=regex_style,
                ),
            },
            extraction={
                "model": model_name,
                "extracted_at": utc_now_iso(),
                "instrument": str(payload.get("instrument") or "NIFTY50"),
                "attempt": attempt + 1,
            },
            fetch_status="ok",
        )
        validated = validate_record(
            attempt_record,
            body=text,
            used_regex_only=used_regex_target and model_name == "regex",
        )
        last_record = validated
        if validated.fetch_status == "ok":
            return validated

        err = validated.error_message or "validation_failed"
        if pipeline:
            pipeline.warn(
                "extract",
                f"Validation failed (attempt {attempt + 1}): {err}",
                source_id=source.id,
            )
        if attempt >= _MAX_PARSE_RETRIES or model_name == "regex":
            return validated
        validator_errors = [err]

    return last_record
