"""LLM Wiki Deep Research exports for hub news knowledge gaps."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.hub_wiki.bootstrap import ensure_llm_wiki_project
from trade_integrations.dataflows.hub_wiki.client import chat_wiki, trigger_sources_rescan
from trade_integrations.dataflows.hub_wiki.compile import _slug, _yaml_value, event_slug
from trade_integrations.dataflows.hub_wiki.config import (
    get_llm_wiki_project_dir,
    llm_wiki_research_sources_dir,
)
from trade_integrations.dataflows.hub_wiki.research_gaps import detect_research_gaps, pick_primary_gap

logger = logging.getLogger(__name__)

_RESEARCH_LOG_REL = Path("_data") / "news_events" / "research_log.jsonl"


def wiki_deep_research_enabled() -> bool:
    return os.getenv("HUB_NEWS_WIKI_DEEP_RESEARCH", "0").strip().lower() in {"1", "true", "yes", "on"}


def deep_research_max_per_event() -> int:
    try:
        return max(1, int(os.getenv("HUB_NEWS_WIKI_DEEP_RESEARCH_MAX_PER_EVENT", "3")))
    except ValueError:
        return 3


def deep_research_max_per_batch() -> int:
    try:
        return max(1, int(os.getenv("HUB_NEWS_WIKI_DEEP_RESEARCH_MAX_PER_BATCH", "10")))
    except ValueError:
        return 10


def _research_log_path() -> Path:
    path = get_hub_dir() / _RESEARCH_LOG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _today_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _append_research_log(row: dict[str, Any]) -> None:
    path = _research_log_path()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _research_count_today(*, event_id: str, gap_kind: str) -> int:
    path = _research_log_path()
    if not path.is_file():
        return 0
    day = _today_key()
    count = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if (
                str(row.get("event_id") or "") == event_id
                and str(row.get("gap_kind") or "") == gap_kind
                and str(row.get("day") or "") == day
            ):
                count += 1
    except (json.JSONDecodeError, OSError):
        return count
    return count


def _event_research_count_today(event_id: str) -> int:
    path = _research_log_path()
    if not path.is_file():
        return 0
    day = _today_key()
    count = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("event_id") or "") == event_id and str(row.get("day") or "") == day:
                count += 1
    except (json.JSONDecodeError, OSError):
        return count
    return count


def research_already_run_today(*, event_id: str, gap_kind: str) -> bool:
    return _research_count_today(event_id=event_id, gap_kind=gap_kind) >= 1


def _event_meta(event: dict[str, Any]) -> dict[str, Any]:
    structured = event.get("structured_summary") if isinstance(event.get("structured_summary"), dict) else {}
    meta = structured.get("event_meta") if isinstance(structured.get("event_meta"), dict) else {}
    return meta


def _consensus(event: dict[str, Any]) -> dict[str, Any]:
    meta = _event_meta(event)
    consensus = meta.get("consensus") if isinstance(meta.get("consensus"), dict) else {}
    if consensus:
        return consensus
    raw = event.get("consensus")
    return raw if isinstance(raw, dict) else {}


def build_research_prompt(event: dict[str, Any], *, gap: dict[str, Any]) -> str:
    consensus = _consensus(event)
    title = str(event.get("title") or "Market event")
    content = str(event.get("content") or "").strip()
    gap_kind = str(gap.get("gap_kind") or "unknown")
    conflicts = consensus.get("conflicts") or []
    ticker = str(event.get("ticker") or "NIFTY")

    lines = [
        f"India market news research for {ticker}.",
        f"Event: {title}",
        f"Gap: {gap_kind} — {gap.get('detail') or ''}",
        "",
        "Distilled summary:",
        content or "(no content)",
    ]
    if conflicts:
        lines.extend(["", "Source conflicts to reconcile:"])
        for row in conflicts[:8]:
            lines.append(f"- {row}")
    lines.extend(
        [
            "",
            "Question: What is the verified India market impact of this event?",
            "Focus on NIFTY/Sensex relevance, factor links (FII, oil, RBI, earnings),",
            "and cite credible sources. Flag remaining uncertainty explicitly.",
        ]
    )
    purpose_path = get_llm_wiki_project_dir() / "purpose.md"
    if purpose_path.is_file():
        try:
            purpose = purpose_path.read_text(encoding="utf-8").strip()
            if purpose:
                lines.extend(["", "Wiki scope (purpose.md):", purpose[:1200]])
        except OSError:
            pass
    return "\n".join(lines)


def research_slug(event: dict[str, Any], *, gap_kind: str) -> str:
    base = event_slug(event)
    suffix = _slug(gap_kind)[:16]
    return _slug(f"{base}-{suffix}")[:64]


def render_research_source(
    *,
    event: dict[str, Any],
    gap: dict[str, Any],
    answer: str,
    source_rel_path: str,
    chat_meta: dict[str, Any] | None = None,
) -> str:
    meta = _event_meta(event)
    event_id = str(event.get("event_id") or meta.get("event_id") or "")
    title = str(event.get("title") or "Market event")
    gap_kind = str(gap.get("gap_kind") or "unknown")
    now = datetime.now(timezone.utc).isoformat()
    fm = [
        "---",
        "type: research",
        f"title: {_yaml_value(f'Research: {title}')}",
        f"sources: [{_yaml_value(source_rel_path)}]",
        f"event_id: {_yaml_value(event_id)}",
        f"parent_event_id: {_yaml_value(meta.get('parent_event_id'))}",
        f"ticker: {_yaml_value(event.get('ticker') or 'NIFTY')}",
        f"gap_kind: {_yaml_value(gap_kind)}",
        f"research_at: {_yaml_value(now)}",
        f"compiled_at: {_yaml_value(now)}",
        "---",
        "",
        f"# Deep Research: {title}",
        "",
        f"**Gap:** {gap_kind} — {gap.get('detail') or ''}",
        "",
        str(answer or "").strip(),
        "",
    ]
    refs = (chat_meta or {}).get("references") or (chat_meta or {}).get("citations") or []
    if isinstance(refs, list) and refs:
        fm.extend(["## References", ""])
        for ref in refs[:15]:
            if isinstance(ref, dict):
                fm.append(f"- {ref.get('title') or ref.get('path') or ref}")
            else:
                fm.append(f"- {ref}")
        fm.append("")
    return "\n".join(fm)


def export_research_for_event(
    event: dict[str, Any],
    *,
    gap: dict[str, Any] | None = None,
    rescan: bool = False,
) -> dict[str, Any]:
    """Run Deep Research for one event gap and write raw/sources/research/ export."""
    if not wiki_deep_research_enabled():
        return {"ok": False, "skipped": True, "reason": "HUB_NEWS_WIKI_DEEP_RESEARCH disabled"}

    event_id = str(event.get("event_id") or "").strip()
    if not event_id:
        return {"ok": False, "error": "missing event_id"}

    gaps = detect_research_gaps(event)
    chosen = gap or pick_primary_gap(gaps)
    if not chosen:
        return {"ok": False, "skipped": True, "reason": "no_gaps"}

    gap_kind = str(chosen.get("gap_kind") or "unknown")
    if research_already_run_today(event_id=event_id, gap_kind=gap_kind):
        return {"ok": False, "skipped": True, "reason": "already_run_today", "gap_kind": gap_kind}
    if _event_research_count_today(event_id) >= deep_research_max_per_event():
        return {"ok": False, "skipped": True, "reason": "rate_limited", "gap_kind": gap_kind}

    prompt = build_research_prompt(event, gap=chosen)
    chat = chat_wiki(prompt, mode="deep")
    if not chat.get("ok"):
        return {"ok": False, "error": chat.get("error") or "chat_failed", "chat": chat}

    answer = str(
        chat.get("message")
        or chat.get("content")
        or chat.get("assistantMessage")
        or chat.get("text")
        or ""
    ).strip()
    if not answer:
        return {"ok": False, "error": "empty_chat_response", "chat": chat}

    ensure_llm_wiki_project()
    slug = research_slug(event, gap_kind=gap_kind)
    research_dir = llm_wiki_research_sources_dir()
    research_dir.mkdir(parents=True, exist_ok=True)
    source_rel = f"research/{slug}.md"
    md_path = research_dir / f"{slug}.md"
    md_path.write_text(
        render_research_source(
            event=event,
            gap=chosen,
            answer=answer,
            source_rel_path=source_rel,
            chat_meta=chat,
        ),
        encoding="utf-8",
    )
    legacy_json = research_dir / f"{slug}.json"
    if legacy_json.is_file():
        legacy_json.unlink()

    _append_research_log(
        {
            "day": _today_key(),
            "event_id": event_id,
            "gap_kind": gap_kind,
            "slug": slug,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )

    out: dict[str, Any] = {
        "ok": True,
        "event_id": event_id,
        "gap_kind": gap_kind,
        "slug": slug,
        "source_md_path": str(md_path),
    }
    if rescan:
        out["rescan"] = trigger_sources_rescan()
    return out


_batch_research_count = 0


def reset_batch_research_count() -> None:
    global _batch_research_count
    _batch_research_count = 0


def maybe_research_event_gaps(
    event: dict[str, Any],
    *,
    rescan: bool = False,
) -> dict[str, Any] | None:
    """Run Deep Research when gaps exist; respects batch rate limits."""
    global _batch_research_count
    if not wiki_deep_research_enabled():
        return None
    if _batch_research_count >= deep_research_max_per_batch():
        return {"ok": False, "skipped": True, "reason": "batch_rate_limited"}
    gaps = detect_research_gaps(event)
    gap = pick_primary_gap(gaps)
    if not gap:
        return None
    gap_kind = str(gap.get("gap_kind") or "")
    if research_already_run_today(event_id=str(event.get("event_id") or ""), gap_kind=gap_kind):
        return {"ok": False, "skipped": True, "reason": "already_run_today", "gap_kind": gap_kind}
    try:
        result = export_research_for_event(event, gap=gap, rescan=rescan)
    except Exception as exc:
        logger.debug("deep research skipped for %s: %s", event.get("event_id"), exc)
        return {"ok": False, "error": str(exc)}
    if result.get("ok"):
        _batch_research_count += 1
    return result
