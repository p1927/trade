"""Append-only staging queue for raw news refs before entity distillation."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir

_STAGING_DIR = Path("_data") / "news_staging"
_PENDING_FILE = "pending.jsonl"
_MERGED_FILE = "merged.jsonl"
_DISCARDED_FILE = "discarded.jsonl"
_DISCARDED_INDEX_FILE = "discarded_index.json"


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


def _discarded_path() -> Path:
    return _staging_dir() / _DISCARDED_FILE


def _discarded_index_path() -> Path:
    return _staging_dir() / _DISCARDED_INDEX_FILE


def _discard_retention_days() -> int:
    from trade_integrations.dataflows.index_research.news_relevance import discard_retention_days

    return discard_retention_days()


def _discard_id_for(*parts: str) -> str:
    raw = "|".join(p.strip() for p in parts if p and str(p).strip())
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"disc:{digest}"


def _load_discarded_index() -> dict[str, Any]:
    path = _discarded_index_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_discarded_index(index: dict[str, Any]) -> None:
    path = _discarded_index_path()
    path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


def append_discarded_record(
    *,
    source_kind: str,
    ticker: str,
    title: str,
    url: str = "",
    reason: str = "",
    relevance: dict[str, Any] | None = None,
    ref_id: str = "",
    event_id: str = "",
    restore_payload: dict[str, Any] | None = None,
    discard_id: str = "",
) -> dict[str, Any]:
    """Append soft-discard row (Option C) with restore payload."""
    now = _now_iso()
    retention = _discard_retention_days()
    expires = (datetime.now(timezone.utc) + timedelta(days=retention)).isoformat()
    did = discard_id or _discard_id_for(ref_id or event_id or url or title, now)
    row = {
        "discard_id": did,
        "source_kind": source_kind,
        "ref_id": ref_id,
        "event_id": event_id,
        "ticker": (ticker or "NIFTY").strip().upper(),
        "title": str(title or "")[:500],
        "url": str(url or ""),
        "reason": str(reason or "")[:500],
        "relevance": relevance or {},
        "restore_payload": restore_payload or {},
        "discarded_at": now,
        "expires_at": expires,
        "undone_at": None,
    }
    path = _discarded_path()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")
    index = _load_discarded_index()
    index[did] = {"discarded_at": now, "undone_at": None, "ref_id": ref_id, "event_id": event_id}
    _save_discarded_index(index)
    return row


def mark_ref_discarded(
    ref_id: str,
    *,
    reason: str,
    relevance: dict[str, Any] | None = None,
    restore_payload: dict[str, Any] | None = None,
    source_kind: str = "auto_gate",
) -> dict[str, Any] | None:
    """Remove ref from pending queue and append to discarded ledger."""
    pending_path = _staging_dir() / _PENDING_FILE
    if not pending_path.is_file():
        return None
    updated_lines: list[str] = []
    discarded_row: dict[str, Any] | None = None
    for line in pending_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            updated_lines.append(line)
            continue
        if row.get("ref_id") == ref_id:
            payload = restore_payload if restore_payload is not None else dict(row)
            discarded_row = append_discarded_record(
                source_kind=source_kind,
                ticker=str(row.get("ticker") or "NIFTY"),
                title=str(row.get("title") or ""),
                url=str(row.get("url") or ""),
                reason=reason,
                relevance=relevance,
                ref_id=ref_id,
                restore_payload=payload,
            )
            continue
        updated_lines.append(json.dumps(row, default=str))
    pending_path.write_text(
        "\n".join(updated_lines) + ("\n" if updated_lines else ""),
        encoding="utf-8",
    )
    return discarded_row


def list_discarded_refs(
    *,
    ticker: str | None = None,
    limit: int = 100,
    include_expired: bool = False,
    include_undone: bool = False,
) -> list[dict[str, Any]]:
    path = _discarded_path()
    if not path.is_file():
        return []
    sym = (ticker or "").strip().upper()
    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not include_undone and row.get("undone_at"):
            continue
        if sym and str(row.get("ticker") or "").upper() != sym:
            continue
        expires_raw = str(row.get("expires_at") or "")
        if not include_expired and expires_raw:
            try:
                exp = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp < now:
                    continue
            except ValueError:
                pass
        out.append(row)
    out.sort(key=lambda r: str(r.get("discarded_at") or ""), reverse=True)
    return out[:limit]


def discarded_count(*, ticker: str | None = None) -> int:
    return len(list_discarded_refs(ticker=ticker, limit=10_000))


def get_discarded_record(discard_id: str) -> dict[str, Any] | None:
    did = str(discard_id or "").strip()
    if not did:
        return None
    for row in list_discarded_refs(limit=10_000, include_expired=True, include_undone=True):
        if str(row.get("discard_id") or "") == did:
            return row
    return None


def restore_discarded(discard_id: str) -> dict[str, Any]:
    """Mark discard undone and re-enqueue staging ref when applicable."""
    record = get_discarded_record(discard_id)
    if not record:
        raise ValueError(f"discard_id not found: {discard_id}")
    if record.get("undone_at"):
        return {"restored": False, "reason": "already undone", "discard_id": discard_id}

    now = _now_iso()
    expires_raw = str(record.get("expires_at") or "")
    if expires_raw:
        try:
            exp = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                return {"restored": False, "reason": "expired", "discard_id": discard_id}
        except ValueError:
            pass

    path = _discarded_path()
    lines: list[str] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if str(row.get("discard_id") or "") == discard_id:
                row["undone_at"] = now
                lines.append(json.dumps(row, default=str))
            else:
                lines.append(json.dumps(row, default=str))
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    index = _load_discarded_index()
    if discard_id in index:
        index[discard_id]["undone_at"] = now
        _save_discarded_index(index)

    payload = record.get("restore_payload") or {}
    source_kind = str(record.get("source_kind") or "")
    restored_kind = "none"
    if payload.get("ref_id") and source_kind in {"staging", "auto_gate", "manual", "cleanup"}:
        pending_path = _staging_dir() / _PENDING_FILE
        restore = dict(payload)
        restore["status"] = "queued"
        restore["merged_into"] = ""
        with pending_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(restore, default=str) + "\n")
        restored_kind = "staging"
    elif record.get("event_id") and isinstance(payload, dict) and payload.get("event_id"):
        from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent
        from trade_integrations.hub_storage.news_events_store import upsert_event

        event = DistilledNewsEvent.from_dict(payload)
        event.status = "active"
        upsert_event(event)
        restored_kind = "event"

    return {
        "restored": True,
        "discard_id": discard_id,
        "restored_kind": restored_kind,
        "ref_id": record.get("ref_id"),
        "event_id": record.get("event_id"),
    }


def purge_expired_discarded() -> int:
    path = _discarded_path()
    if not path.is_file():
        return 0
    now = datetime.now(timezone.utc)
    kept: list[str] = []
    removed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if row.get("undone_at"):
            kept.append(json.dumps(row, default=str))
            continue
        expires_raw = str(row.get("expires_at") or "")
        if not expires_raw:
            kept.append(json.dumps(row, default=str))
            continue
        try:
            exp = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < now:
                removed += 1
                continue
        except ValueError:
            pass
        kept.append(json.dumps(row, default=str))
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    if removed:
        active_ids = set()
        for line in kept:
            try:
                row = json.loads(line)
                did = str(row.get("discard_id") or "")
                if did:
                    active_ids.add(did)
            except json.JSONDecodeError:
                pass
        index = _load_discarded_index()
        for did in list(index.keys()):
            if did not in active_ids:
                del index[did]
        _save_discarded_index(index)
    return removed


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


def _parse_iso_age_seconds(iso_value: str) -> float | None:
    raw = (iso_value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        created = datetime.fromisoformat(raw)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0.0, (now - created).total_seconds())
    except ValueError:
        return None


def staging_queue_detail(*, ticker: str | None = None) -> dict[str, Any]:
    """Queue depth plus oldest pending ref age (seconds)."""
    pending = list_pending_refs(ticker=ticker, limit=10_000)
    ages = [_parse_iso_age_seconds(str(row.get("created_at") or "")) for row in pending]
    ages = [age for age in ages if age is not None]
    oldest_seconds = max(ages) if ages else 0.0
    return {
        "queued": len(pending),
        "oldest_pending_seconds": round(oldest_seconds, 1),
    }


def is_entity_pipeline_enabled() -> bool:
    return os.getenv("HUB_NEWS_ENTITY_PIPELINE", "1").strip().lower() in {"1", "true", "yes", "on"}


def minimax_configured() -> bool:
    """True when MiniMax API credentials are available for distillation."""
    from trade_integrations.nse_browser.minimax_agent import minimax_configured as _configured

    return _configured()


def is_legacy_ingest_enabled() -> bool:
    """Force direct verify path (skip staging) when ``HUB_NEWS_LEGACY_INGEST=1``."""
    return os.getenv("HUB_NEWS_LEGACY_INGEST", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def rule_fallback_distillation_enabled() -> bool:
    return os.getenv("HUB_NEWS_RULE_FALLBACK_DISTILL", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def pipeline_pause_status(*, ticker: str | None = None) -> dict[str, Any]:
    """Return whether entity distillation is paused and why."""
    pending = staging_queue_detail(ticker=ticker)
    if not is_entity_pipeline_enabled():
        return {
            "pipeline_paused": False,
            "pause_reason": "",
            "minimax_configured": minimax_configured(),
            "rule_fallback_enabled": rule_fallback_distillation_enabled(),
            "pending": pending,
        }
    if minimax_configured() or rule_fallback_distillation_enabled():
        return {
            "pipeline_paused": False,
            "pause_reason": "",
            "minimax_configured": minimax_configured(),
            "rule_fallback_enabled": rule_fallback_distillation_enabled(),
            "pending": pending,
        }
    return {
        "pipeline_paused": True,
        "pause_reason": (
            "MINIMAX_API_KEY is not configured and rule-fallback distillation is disabled. "
            "Set MINIMAX_API_KEY or HUB_NEWS_RULE_FALLBACK_DISTILL=1 in .env."
        ),
        "minimax_configured": False,
        "rule_fallback_enabled": False,
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
    """Fail fast when neither MiniMax nor rule-fallback distillation is available."""
    if minimax_configured() or rule_fallback_distillation_enabled():
        return
    raise RuntimeError(
        "MINIMAX_API_KEY is required for hub news entity distillation unless "
        "HUB_NEWS_RULE_FALLBACK_DISTILL=1 is set."
    )
