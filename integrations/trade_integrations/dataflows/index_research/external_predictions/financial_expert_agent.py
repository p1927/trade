"""Financial expert agent — multimodal extract + vision cross-check for street forecasts."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from trade_integrations.dataflows.index_research.external_predictions.extractor import (
    extract_prediction_from_text,
)
from trade_integrations.dataflows.index_research.external_predictions.financial_expert_context import (
    load_expert_context,
)
from trade_integrations.dataflows.index_research.external_predictions.minimax_vision import (
    call_minimax_vision_json,
    vision_cross_check,
    vision_enabled,
)
from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionSource,
    ExternalPredictionTarget,
    utc_now_iso,
)
from trade_integrations.dataflows.index_research.external_predictions.screenshot_utils import (
    ScreenshotArtifacts,
    jpeg_file_to_b64,
)
from trade_integrations.dataflows.index_research.external_predictions.validators import (
    validate_record,
)
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3


def _m3_images(artifacts: ScreenshotArtifacts | None) -> list[str]:
    if artifacts is None:
        return []
    out: list[str] = []
    for path in artifacts.m3_paths:
        if path.is_file():
            out.append(jpeg_file_to_b64(path))
    return out


def _build_expert_prompt(
    *,
    source: ExternalPredictionSource,
    horizon_days: int,
    spot: float | None,
    title: str,
    url: str,
    body: str,
    expert_context: dict[str, Any],
    validator_errors: list[str] | None = None,
    vision_errors: list[str] | None = None,
) -> str:
    spot_line = f"Current NIFTY spot reference: {spot:.0f}" if spot else "Current NIFTY spot: unknown"
    retry_lines: list[str] = []
    if validator_errors:
        retry_lines.append("Previous validation failed:\n- " + "\n- ".join(validator_errors))
    if vision_errors:
        retry_lines.append("Previous vision check failed:\n- " + "\n- ".join(vision_errors))
    retry_block = "\n".join(retry_lines)
    if retry_block:
        retry_block = retry_block + "\nFix these issues in your JSON output.\n"

    ctx_blob = ""
    brief = expert_context.get("expert_brief")
    if isinstance(brief, str) and brief.strip():
        ctx_blob = brief.strip()[:4000]
    movers = expert_context.get("top_factor_movers") or []
    mover_lines = ""
    if isinstance(movers, list) and movers:
        mover_lines = "Top factor movers: " + ", ".join(
            str(row.get("factor") or row) if isinstance(row, dict) else str(row)
            for row in movers[:5]
        )

    return f"""You are a financial expert extracting a structured NIFTY 50 index forecast.

Source: {source.display_name}
User-selected horizon: {horizon_days} trading days
{spot_line}
URL: {url}
Title: {title}

Expert context:
{ctx_blob}
{mover_lines}

{retry_block}
Use the article text AND screenshot(s). Return ONLY valid JSON:
{{
  "has_prediction": true,
  "instrument": "NIFTY50",
  "target_low": number or null,
  "target_mid": number or null,
  "target_high": number or null,
  "target_date": "YYYY-MM-DD or empty",
  "direction": "bullish" | "bearish" | "neutral",
  "expected_return_pct": number or null,
  "summary": "2-3 sentence overview",
  "rationale_bullets": ["driver 1", "driver 2"],
  "confidence": "high" | "medium" | "low",
  "published_at": "YYYY-MM-DD or empty"
}}

Rules:
- ONLY NIFTY 50 index level targets (15000-35000).
- REJECT single-stock targets and options commentary without index level.
- Do NOT treat technical resistance/support levels as analyst price targets unless explicitly labeled as a NIFTY target.
- Extract target_date even when it differs from the user-selected horizon.

Article text:
{body[:8000]}
"""


def _record_from_payload(
    *,
    source: ExternalPredictionSource,
    symbol: str,
    horizon_days: int,
    spot: float | None,
    title: str,
    url: str,
    snippet: str,
    payload: dict[str, Any],
    attempt: int,
) -> ExternalPredictionRecord:
    from trade_integrations.dataflows.index_research.external_predictions.extractor import (
        _maybe_float,
        _normalize_level,
    )

    target = ExternalPredictionTarget(
        low=_normalize_level(_maybe_float(payload.get("target_low")), spot),
        mid=_normalize_level(_maybe_float(payload.get("target_mid")), spot),
        high=_normalize_level(_maybe_float(payload.get("target_high")), spot),
    )
    direction = str(payload.get("direction") or "neutral")
    if direction not in {"bullish", "bearish", "neutral"}:
        direction = "neutral"
    bullets = payload.get("rationale_bullets") or []
    if not isinstance(bullets, list):
        bullets = []
    bullets = [str(b).strip() for b in bullets if str(b).strip()]
    confidence = str(payload.get("confidence") or "medium")
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    expected_return = _maybe_float(payload.get("expected_return_pct"))
    if expected_return is None and spot and target.mid:
        expected_return = round((target.mid - spot) / spot * 100, 2)

    return ExternalPredictionRecord(
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
        published_at=str(payload.get("published_at") or "")[:10],
        provenance={
            "url": url,
            "title": title,
            "snippet": snippet[:500],
            "summary": str(payload.get("summary") or "").strip(),
            "instrument": str(payload.get("instrument") or "NIFTY50"),
        },
        extraction={
            "model": "minimax-m3-vision",
            "extracted_at": utc_now_iso(),
            "attempt": attempt,
        },
        fetch_status="ok",
    )


def extract_forecast(
    *,
    source: ExternalPredictionSource,
    horizon_days: int,
    spot: float | None,
    title: str,
    url: str,
    snippet: str,
    body: str,
    symbol: str = "NIFTY",
    screenshot_artifacts: ScreenshotArtifacts | None = None,
    pipeline: PipelineLogger | None = None,
) -> ExternalPredictionRecord:
    """Extract via financial expert agent when vision is enabled; else text-only path."""
    images = _m3_images(screenshot_artifacts)
    if not vision_enabled() or not images:
        record = extract_prediction_from_text(
            source=source,
            horizon_days=horizon_days,
            spot=spot,
            title=title,
            url=url,
            snippet=snippet,
            body=body,
            symbol=symbol,
            pipeline=pipeline,
        )
        if screenshot_artifacts is not None:
            _attach_artifact_provenance(record, screenshot_artifacts, symbol=symbol)
        return record

    expert_context = load_expert_context(symbol=symbol) or {}
    validator_errors: list[str] = []
    vision_errors: list[str] = []
    last_record = ExternalPredictionRecord(
        source_id=source.id,
        symbol=symbol.upper(),
        horizon_days=horizon_days,
        fetch_status="not_found",
    )

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        prompt = _build_expert_prompt(
            source=source,
            horizon_days=horizon_days,
            spot=spot,
            title=title,
            url=url,
            body=body,
            expert_context=expert_context,
            validator_errors=validator_errors or None,
            vision_errors=vision_errors or None,
        )
        if pipeline:
            pipeline.info(
                "expert_agent",
                "MiniMax M3 vision extraction",
                source_id=source.id,
                attempt=attempt,
                tiles=len(images),
            )
        try:
            payload = call_minimax_vision_json(
                system_prompt=(
                    "Extract NIFTY 50 index forecasts from news pages using text and screenshots. "
                    "Output strict JSON only."
                ),
                user_text=prompt,
                image_jpeg_b64_list=images,
                max_tokens=1600,
            )
        except Exception as exc:
            if pipeline:
                pipeline.warn(
                    "expert_agent",
                    f"Vision extract failed — text fallback: {exc}",
                    source_id=source.id,
                )
            record = extract_prediction_from_text(
                source=source,
                horizon_days=horizon_days,
                spot=spot,
                title=title,
                url=url,
                snippet=snippet,
                body=body,
                symbol=symbol,
                pipeline=pipeline,
            )
            if screenshot_artifacts is not None:
                _attach_artifact_provenance(record, screenshot_artifacts, symbol=symbol)
            return record

        record = _record_from_payload(
            source=source,
            symbol=symbol,
            horizon_days=horizon_days,
            spot=spot,
            title=title,
            url=url,
            snippet=snippet,
            payload=payload,
            attempt=attempt,
        )
        if not any(v is not None for v in (record.target.low, record.target.mid, record.target.high)):
            record.fetch_status = "not_found"
            record.error_message = "No numeric NIFTY target extracted"
            last_record = record
            validator_errors = [record.error_message]
            continue

        validated = validate_record(record, body=body, used_regex_only=False)
        last_record = validated
        if validated.fetch_status != "ok":
            err = validated.error_message or "validation_failed"
            if pipeline:
                pipeline.warn("expert_agent", f"Validation failed: {err}", source_id=source.id)
            validator_errors = [err]
            continue

        ok, vision_err = vision_cross_check(
            target_mid=validated.target.mid,
            target_date=validated.target_date,
            direction=validated.direction,
            image_jpeg_b64_list=images,
            url=url,
            title=title,
        )
        if not ok:
            if pipeline:
                pipeline.warn(
                    "expert_agent",
                    f"Vision cross-check failed: {vision_err}",
                    source_id=source.id,
                )
            validated.fetch_status = "not_found"
            validated.error_message = vision_err or "vision_mismatch"
            last_record = validated
            vision_errors = [vision_err or "vision_mismatch"]
            continue

        if screenshot_artifacts is not None:
            _attach_artifact_provenance(validated, screenshot_artifacts, symbol=symbol)
        validated.extraction = {
            **dict(validated.extraction or {}),
            "vision_checked": True,
        }
        return validated

    if screenshot_artifacts is not None:
        _attach_artifact_provenance(last_record, screenshot_artifacts, symbol=symbol)
    return last_record


def _attach_artifact_provenance(
    record: ExternalPredictionRecord,
    artifacts: ScreenshotArtifacts,
    *,
    symbol: str,
) -> None:
    sym = symbol.upper()
    rel_thumb = ""
    if artifacts.thumbnail_path and artifacts.thumbnail_path.is_file():
        try:
            from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
                external_predictions_root,
            )

            rel_thumb = str(artifacts.thumbnail_path.relative_to(external_predictions_root(sym)))
        except ValueError:
            rel_thumb = str(artifacts.thumbnail_path)
    record.provenance = {
        **dict(record.provenance or {}),
        "artifact_run_id": artifacts.run_id,
        "thumbnail_path": rel_thumb,
        "thumbnail_url": artifacts.thumbnail_api_path(symbol=sym, source_id=record.source_id),
        "screenshot_tiles": len(artifacts.m3_paths),
    }
