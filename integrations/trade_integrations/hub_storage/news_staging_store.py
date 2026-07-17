"""Append-only staging queue for raw news refs before entity distillation."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir

_STAGING_DIR = Path("_data") / "news_staging"
_PENDING_FILE = "pending.jsonl"
_MERGED_FILE = "merged.jsonl"


def _url_dedupe_key(url: str) -> str:
    """Canonical URL key for cross-source duplicate detection."""
    from trade_integrations.dataflows.news_aggregator.dedup import normalize_url

    raw = (url or "").strip()
    if not raw:
        return ""
    normalized = normalize_url(raw)
    return normalized or raw.lower()


def _staging_dir() -> Path:
    path = get_hub_dir() / _STAGING_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ref_id_for_url(url: str) -> str:
    normalized = (url or "").strip().lower()
    if not normalized:
        normalized = hashlib.sha256(_now_iso().encode()).hexdigest()[:16]
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return f"ref:{digest}"


def enqueue_raw_ref(row: dict[str, Any], *, ticker: str) -> tuple[str, bool]:
    """Append a raw article ref to the staging queue; skip exact URL duplicates.

    Returns ``(ref_id, appended)`` where ``appended`` is False when deduped.
    """
    url = str(row.get("url") or "").strip()
    ref_id = ref_id_for_url(url or str(row.get("title") or ""))
    url_key = _url_dedupe_key(url)
    pending_path = _staging_dir() / _PENDING_FILE

    if pending_path.is_file():
        for line in pending_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                existing = json.loads(line)
            except json.JSONDecodeError:
                continue
            existing_url = str(existing.get("url") or "").strip()
            if existing.get("ref_id") == ref_id or (
                url_key and _url_dedupe_key(existing_url) == url_key
            ):
                return ref_id, False

    payload = {
        "ref_id": ref_id,
        "ticker": (ticker or "NIFTY").strip().upper(),
        "title": str(row.get("title") or "")[:500],
        "summary": str(row.get("summary") or "")[:2000],
        "url": url,
        "source": str(row.get("source") or "unknown"),
        "published_at": str(row.get("published_at") or _now_iso()),
        "sources": row.get("sources") if isinstance(row.get("sources"), list) else [],
        "tags": row.get("tags") if isinstance(row.get("tags"), dict) else {},
        "status": "queued",
        "merged_into": "",
        "created_at": _now_iso(),
    }
    with pending_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")
    return ref_id, True


def list_pending_refs(*, ticker: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    pending_path = _staging_dir() / _PENDING_FILE
    if not pending_path.is_file():
        return []
    sym = (ticker or "").strip().upper()
    out: list[dict[str, Any]] = []
    for line in pending_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") != "queued":
            continue
        if sym and str(row.get("ticker") or "").upper() != sym:
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def mark_ref_merged(ref_id: str, event_id: str) -> None:
    pending_path = _staging_dir() / _PENDING_FILE
    if not pending_path.is_file():
        return
    merged_path = _staging_dir() / _MERGED_FILE
    updated_lines: list[str] = []
    merged_row: dict[str, Any] | None = None
    for line in pending_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            updated_lines.append(line)
            continue
        if row.get("ref_id") == ref_id:
            row["status"] = "merged"
            row["merged_into"] = event_id
            row["merged_at"] = _now_iso()
            merged_row = row
            continue
        updated_lines.append(json.dumps(row, default=str))
    pending_path.write_text(
        "\n".join(updated_lines) + ("\n" if updated_lines else ""),
        encoding="utf-8",
    )
    if merged_row:
        with merged_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(merged_row, default=str) + "\n")


def staging_ref_to_headline(row: dict[str, Any]) -> dict[str, Any]:
    """Map staging ref to hub-compatible headline dict for live reads."""
    return {
        "canonical_story_id": row.get("ref_id") or "",
        "id": row.get("ref_id") or "",
        "ticker": row.get("ticker") or "NIFTY",
        "title": row.get("title") or "",
        "content_summary": row.get("summary") or "",
        "summary": row.get("summary") or "",
        "sources": row.get("sources") or [],
        "published_at": row.get("published_at") or "",
        "tags": row.get("tags") or {},
        "verification_status": "pending",
        "provenance": "staging",
        "url": row.get("url") or "",
        "source": row.get("source") or "staging",
    }


def staging_queue_stats(*, ticker: str | None = None) -> dict[str, int]:
    pending = list_pending_refs(ticker=ticker, limit=10_000)
    return {"queued": len(pending)}


def is_entity_pipeline_enabled() -> bool:
    return os.getenv("HUB_NEWS_ENTITY_PIPELINE", "1").strip().lower() in {"1", "true", "yes", "on"}


def minimax_configured() -> bool:
    """True when MiniMax API credentials are available for distillation."""
    from trade_integrations.nse_browser.minimax_agent import minimax_configured as _configured

    return _configured()


def pipeline_pause_status(*, ticker: str | None = None) -> dict[str, Any]:
    """Return whether entity distillation is paused and why."""
    pending = staging_queue_stats(ticker=ticker)
    if not is_entity_pipeline_enabled():
        return {
            "pipeline_paused": False,
            "pause_reason": "",
            "minimax_configured": minimax_configured(),
            "pending": pending,
        }
    if minimax_configured():
        return {
            "pipeline_paused": False,
            "pause_reason": "",
            "minimax_configured": True,
            "pending": pending,
        }
    return {
        "pipeline_paused": True,
        "pause_reason": (
            "MINIMAX_API_KEY is not configured. Set MINIMAX_API_KEY and MINIMAX_BASE_URL in .env, "
            "then drain staging to process queued headlines."
        ),
        "minimax_configured": False,
        "pending": pending,
    }


def collect_distilled_urls(records: list[dict[str, Any]]) -> set[str]:
    """Normalized article URLs already present in distilled/verified records."""
    seen: set[str] = set()
    for rec in records:
        key = _url_dedupe_key(str(rec.get("url") or ""))
        if key:
            seen.add(key)
        for src in rec.get("sources") or []:
            if isinstance(src, dict):
                src_key = _url_dedupe_key(str(src.get("url") or ""))
                if src_key:
                    seen.add(src_key)
    return seen


def filter_staging_refs_not_in_urls(
    refs: list[dict[str, Any]],
    seen_urls: set[str],
) -> list[dict[str, Any]]:
    """Drop staging refs whose URL already appears in verified/distilled records."""
    out: list[dict[str, Any]] = []
    for ref in refs:
        key = _url_dedupe_key(str(ref.get("url") or ""))
        if key and key in seen_urls:
            continue
        out.append(ref)
    return out


def llm_distillation_enabled() -> bool:
    """Alias: entity pipeline on implies LLM distillation when processing staging refs."""
    return is_entity_pipeline_enabled()


def require_minimax_for_distillation() -> None:
    """Entity processing always distills via MiniMax — fail fast if unavailable."""
    if not minimax_configured():
        raise RuntimeError(
            "MINIMAX_API_KEY is required for hub news entity distillation. "
            "Set MINIMAX_API_KEY and MINIMAX_BASE_URL in .env."
        )
