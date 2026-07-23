"""LLM Wiki hybrid search for hub news dedup and enrichment."""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from trade_integrations.dataflows.hub_wiki.client import (
    health_check,
    project_path_aligned,
    search_wiki,
)
from trade_integrations.dataflows.hub_wiki.config import (
    get_llm_wiki_project_dir,
    llm_wiki_news_sources_dir,
)
from trade_integrations.dataflows.index_research.news_dedup import publish_day_from_value
from trade_integrations.dataflows.index_research.news_event_matching import (
    find_matching_event,
    match_threshold,
    title_similarity,
)
from trade_integrations.dataflows.index_research.news_parent_events import (
    infer_parent_event_id,
)

logger = logging.getLogger(__name__)

_EVENT_ID_FM = re.compile(r"^event_id:\s*(.+)$", re.MULTILINE)
_SAFE_SLUG_RE = re.compile(r"^[a-z0-9-]+$")

_availability_cache: bool | None = None


def reset_wiki_search_availability_cache() -> None:
    global _availability_cache
    _availability_cache = None


def wiki_search_enabled() -> bool:
    raw = os.getenv("HUB_NEWS_WIKI_SEARCH_ENABLED", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def wiki_search_top_k() -> int:
    try:
        return int(os.getenv("HUB_NEWS_WIKI_SEARCH_TOP_K", "5"))
    except ValueError:
        return 5


def wiki_search_max_per_pass() -> int:
    try:
        return int(os.getenv("HUB_NEWS_WIKI_SEARCH_MAX_PER_PASS", "150"))
    except ValueError:
        return 150


def wiki_search_min_score() -> float:
    try:
        return float(os.getenv("HUB_NEWS_WIKI_SEARCH_MIN_SCORE", "0.75"))
    except ValueError:
        return 0.75


def wiki_search_available(*, force_refresh: bool = False, enabled: bool | None = None) -> bool:
    global _availability_cache
    if enabled is False:
        return False
    if enabled is None and not wiki_search_enabled():
        return False
    if not force_refresh and _availability_cache is not None:
        return _availability_cache
    try:
        health = health_check()
        if not health.get("ok"):
            _availability_cache = False
            return False
        alignment = project_path_aligned(expected_dir=get_llm_wiki_project_dir())
        _availability_cache = bool(alignment.get("aligned"))
        return _availability_cache
    except Exception as exc:
        logger.debug("wiki search unavailable: %s", exc)
        _availability_cache = False
        return False


def build_search_query(record: dict[str, Any], *, ticker: str = "NIFTY") -> str:
    title = str(record.get("title") or "")
    body = str(
        record.get("content_summary")
        or record.get("content")
        or record.get("summary")
        or ""
    )
    day = publish_day_from_value(
        str(record.get("published_at") or record.get("publish_day") or "")
    )
    sym = str(record.get("ticker") or ticker).strip().upper()
    snippet = body[:200].strip()
    parts = [title, snippet, sym]
    if day:
        parts.append(day)
    return " ".join(p for p in parts if p).strip()


def _parse_frontmatter_event_id(md_path: Path) -> str | None:
    if not md_path.is_file():
        return None
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    block = text[3:end]
    match = _EVENT_ID_FM.search(block)
    if not match:
        return None
    return str(match.group(1)).strip().strip('"').strip("'") or None


def _safe_news_slug(slug: str) -> str | None:
    cleaned = (slug or "").strip().lower()
    if not cleaned or not _SAFE_SLUG_RE.fullmatch(cleaned):
        return None
    return cleaned


def _slug_from_hit_path(path: str) -> str | None:
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        return None
    name = Path(raw).name
    if name.endswith(".md"):
        return _safe_news_slug(name[:-3])
    if name.endswith(".json"):
        return _safe_news_slug(name[:-5])
    return _safe_news_slug(name)


def _sidecar_path(news_dir: Path, slug: str) -> Path | None:
    safe = _safe_news_slug(slug)
    if not safe:
        return None
    resolved = (news_dir / f"{safe}.json").resolve()
    root = news_dir.resolve()
    if not resolved.is_relative_to(root):
        return None
    return resolved


def _md_path(news_dir: Path, slug: str) -> Path | None:
    safe = _safe_news_slug(slug)
    if not safe:
        return None
    resolved = (news_dir / f"{safe}.md").resolve()
    root = news_dir.resolve()
    if not resolved.is_relative_to(root):
        return None
    return resolved


def build_source_event_index(*, news_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Scan raw/sources/news/*.json sidecars into event_id → enrichment map."""
    root = news_dir or llm_wiki_news_sources_dir()
    by_event_id: dict[str, dict[str, Any]] = {}
    by_slug: dict[str, str] = {}
    if not root.is_dir():
        return {"by_event_id": by_event_id, "by_slug": by_slug, "news_dir": str(root)}

    for json_path in root.glob("*.json"):
        slug = json_path.stem
        if not _safe_news_slug(slug):
            continue
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        event_id = str(payload.get("event_id") or "").strip()
        if not event_id:
            md_path = root / f"{slug}.md"
            event_id = _parse_frontmatter_event_id(md_path) or ""
        if not event_id:
            continue
        entry = {
            "event_id": event_id,
            "slug": slug,
            "title": str(payload.get("title") or ""),
            "publish_day": str(payload.get("publish_day") or ""),
            "content_fingerprint": str(payload.get("content_fingerprint") or ""),
            "references": list(payload.get("references") or []),
            "timeline": list(payload.get("timeline") or []),
            "json_path": str(json_path),
            "md_path": str(root / f"{slug}.md"),
        }
        by_event_id[event_id] = entry
        by_slug[slug] = event_id

    return {"by_event_id": by_event_id, "by_slug": by_slug, "news_dir": str(root)}


def _hit_score(hit: dict[str, Any]) -> float:
    for key in ("score", "vectorScore", "similarity", "relevance"):
        val = hit.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return 0.0


def _parquet_event_exists(event_id: str) -> bool:
    from trade_integrations.hub_storage.news_events_store import get_event

    return get_event(event_id) is not None


def resolve_hit_to_event_id(
    hit: dict[str, Any],
    index: dict[str, Any],
) -> str | None:
    """Map a search hit to hub event_id via sidecar index or local frontmatter."""
    by_event_id = index.get("by_event_id") or {}
    by_slug = index.get("by_slug") or {}
    news_dir = Path(str(index.get("news_dir") or llm_wiki_news_sources_dir()))

    direct = str(hit.get("event_id") or hit.get("eventId") or "").strip()
    if direct and (direct in by_event_id or _parquet_event_exists(direct)):
        return direct

    path = str(hit.get("path") or hit.get("filePath") or hit.get("file") or "")
    slug = _slug_from_hit_path(path)
    if slug and slug in by_slug:
        return by_slug[slug]

    if slug:
        json_path = _sidecar_path(news_dir, slug)
        if json_path and json_path.is_file():
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
                eid = str(payload.get("event_id") or "").strip()
                if eid:
                    return eid
            except (OSError, json.JSONDecodeError):
                pass
        md_path = _md_path(news_dir, slug)
        if md_path:
            eid = _parse_frontmatter_event_id(md_path)
            if eid:
                return eid

    return None


def resolved_event_metadata(event_id: str, index: dict[str, Any]) -> dict[str, Any]:
    """Merge sidecar index entry with parquet SSOT for scoring and gates."""
    from trade_integrations.hub_storage.news_events_store import (
        distilled_event_to_headline_dict,
        get_event,
    )

    by_event_id = index.get("by_event_id") or {}
    entry: dict[str, Any] = dict(by_event_id.get(event_id) or {})
    entry.setdefault("event_id", event_id)

    stored = get_event(event_id)
    headline: dict[str, Any] | None = None
    if stored:
        headline = distilled_event_to_headline_dict(stored)
        if not entry.get("title"):
            entry["title"] = headline.get("title") or ""
        if not entry.get("publish_day"):
            entry["publish_day"] = headline.get("publish_day") or publish_day_from_value(
                str(headline.get("published_at") or "")
            )
        entry["headline"] = headline

    return entry


def load_wiki_enrichment(event_id: str, index: dict[str, Any]) -> dict[str, Any]:
    meta = resolved_event_metadata(event_id, index)
    refs = list(meta.get("references") or [])
    urls = [
        str(r.get("url") or "")
        for r in refs
        if isinstance(r, dict) and r.get("url")
    ]
    return {
        "event_id": event_id,
        "references": refs,
        "timeline": list(meta.get("timeline") or []),
        "source_urls": urls,
        "title": meta.get("title") or "",
        "publish_day": meta.get("publish_day") or "",
    }


def _record_as_match_ref(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": record.get("title") or "",
        "summary": record.get("content_summary") or record.get("content") or record.get("summary") or "",
        "url": record.get("url") or "",
        "published_at": record.get("published_at") or record.get("publish_day") or "",
        "tags": record.get("tags") or {},
    }


def _same_day_or_parent_thread(
    query_record: dict[str, Any],
    resolved_event: dict[str, Any],
) -> bool:
    query_day = publish_day_from_value(
        str(query_record.get("published_at") or query_record.get("publish_day") or "")
    )
    resolved_day = str(resolved_event.get("publish_day") or "").strip()
    if not resolved_day:
        resolved_day = publish_day_from_value(
            str(resolved_event.get("published_at") or "")
        )
    ref_tags = query_record.get("tags") if isinstance(query_record.get("tags"), dict) else {}
    ref_parent = infer_parent_event_id(query_record, tags=ref_tags)
    event_parent = str(resolved_event.get("parent_event_id") or "")
    if ref_parent and event_parent and ref_parent == event_parent:
        return True
    if query_day and not resolved_day:
        return False
    if query_day and resolved_day and query_day != resolved_day:
        return False
    return True


def score_wiki_match(
    query_record: dict[str, Any],
    hit: dict[str, Any],
    resolved_event: dict[str, Any],
    *,
    ticker: str = "NIFTY",
    min_score: float | None = None,
) -> float:
    """Return combined score or 0.0 if match should be rejected."""
    cut = wiki_search_min_score() if min_score is None else min_score
    api_score = _hit_score(hit)
    if api_score < cut:
        return 0.0

    if not _same_day_or_parent_thread(query_record, resolved_event):
        return 0.0

    query_title = str(query_record.get("title") or "")
    resolved_title = str(resolved_event.get("title") or "")
    title_sim = title_similarity(query_title, resolved_title)
    if title_sim < match_threshold():
        return 0.0

    headline = resolved_event.get("headline")
    event_id = str(resolved_event.get("event_id") or "").strip()
    if event_id and _parquet_event_exists(event_id):
        if not isinstance(headline, dict):
            return 0.0
        match_ref = _record_as_match_ref(query_record)
        if not find_matching_event(match_ref, [headline], ticker=ticker):
            return 0.0
    elif isinstance(headline, dict):
        match_ref = _record_as_match_ref(query_record)
        if not find_matching_event(match_ref, [headline], ticker=ticker):
            return 0.0

    return max(api_score, title_sim)


def _record_event_id(record: dict[str, Any]) -> str:
    return str(record.get("canonical_story_id") or record.get("event_id") or "").strip()


def _load_record_for_event_id(
    event_id: str,
    *,
    records_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    rec = records_by_id.get(event_id)
    if rec:
        return rec
    from trade_integrations.hub_storage.news_events_store import (
        distilled_event_to_headline_dict,
        get_event,
    )

    stored = get_event(event_id)
    if stored:
        return distilled_event_to_headline_dict(stored)
    return None


def _union_find_root(parent: dict[str, str], node: str) -> str:
    parent.setdefault(node, node)
    while parent[node] != node:
        parent[node] = parent[parent[node]]
        node = parent[node]
    return node


def _union_find_merge(parent: dict[str, str], left: str, right: str) -> None:
    root_left = _union_find_root(parent, left)
    root_right = _union_find_root(parent, right)
    if root_left != root_right:
        parent[root_right] = root_left


def find_wiki_match_for_record(
    record: dict[str, Any],
    *,
    ticker: str = "NIFTY",
    exclude_ids: set[str] | None = None,
    index: dict[str, Any] | None = None,
    top_k: int | None = None,
    min_score: float | None = None,
    wiki_available: bool | None = None,
) -> dict[str, Any] | None:
    """Search wiki for best matching canonical event_id, with enrichment payload."""
    available = wiki_search_available() if wiki_available is None else wiki_available
    if not available:
        return None

    query = build_search_query(record, ticker=ticker)
    if not query:
        return None

    skip = exclude_ids or set()
    record_id = _record_event_id(record)
    if record_id:
        skip = set(skip) | {record_id}

    idx = index or build_source_event_index()

    payload = search_wiki(query, top_k=top_k or wiki_search_top_k())
    if not payload.get("ok"):
        return None

    best: dict[str, Any] | None = None
    best_score = 0.0

    for hit in payload.get("results") or []:
        if not isinstance(hit, dict):
            continue
        event_id = resolve_hit_to_event_id(hit, idx)
        if not event_id or event_id in skip:
            continue

        resolved = resolved_event_metadata(event_id, idx)
        combined = score_wiki_match(
            record,
            hit,
            resolved,
            ticker=ticker,
            min_score=min_score,
        )
        if combined <= best_score:
            continue

        enrichment = load_wiki_enrichment(event_id, idx)
        best_score = combined
        best = {
            "event_id": event_id,
            "score": combined,
            "enrichment": enrichment,
            "hit_path": hit.get("path") or hit.get("filePath"),
        }

    return best


def _wiki_target_for_component(
    member_ids: set[str],
    record_to_target: dict[str, str],
) -> str:
    targets = [record_to_target[k] for k in member_ids if k in record_to_target]
    if targets:
        counts: dict[str, int] = {}
        for target in targets:
            counts[target] = counts.get(target, 0) + 1
        return max(counts, key=lambda k: counts[k])
    for event_id in member_ids:
        if event_id not in record_to_target:
            return event_id
    return next(iter(member_ids))


def build_duplicate_groups_wiki(
    records: list[dict[str, Any]],
    *,
    ticker: str,
    consumed: set[str] | None = None,
    max_queries: int | None = None,
    index: dict[str, Any] | None = None,
    wiki_available: bool | None = None,
    top_k: int | None = None,
    min_score: float | None = None,
) -> tuple[list[tuple[list[dict[str, Any]], str]], dict[str, int]]:
    """Group records when wiki search resolves them to the same canonical event_id.

    Returns ``(members, wiki_target_id)`` tuples — ``wiki_target_id`` is the
    search-resolved canonical that orphans should merge into.
    """
    stats = {"wiki_search_queries": 0, "wiki_hits": 0}
    available = wiki_search_available() if wiki_available is None else wiki_available
    if not available:
        return [], stats

    skip = consumed or set()
    cap = max_queries if max_queries is not None else wiki_search_max_per_pass()
    idx = index or build_source_event_index()
    records_by_id = {_record_event_id(r): r for r in records if _record_event_id(r)}

    available_records: list[dict[str, Any]] = []
    for record in records:
        rid = _record_event_id(record)
        if rid and rid not in skip:
            available_records.append(record)

    if not available_records:
        return [], stats

    def _priority(rec: dict[str, Any]) -> tuple[int, int]:
        refs = len(rec.get("sources") or []) + len(rec.get("references") or [])
        title_len = len(str(rec.get("title") or ""))
        return (refs, title_len)

    available_records.sort(key=_priority)

    record_to_target: dict[str, str] = {}
    queries = 0
    for record in available_records:
        if queries >= cap:
            break
        rid = _record_event_id(record)
        if not rid or rid in record_to_target:
            continue

        queries += 1
        stats["wiki_search_queries"] += 1
        match = find_wiki_match_for_record(
            record,
            ticker=ticker,
            exclude_ids=skip | set(record_to_target.keys()),
            index=idx,
            wiki_available=available,
            top_k=top_k,
            min_score=min_score,
        )
        if not match:
            continue

        target_id = str(match["event_id"])
        if target_id == rid:
            continue

        if _load_record_for_event_id(target_id, records_by_id=records_by_id) is None:
            continue

        stats["wiki_hits"] += 1
        record_to_target[rid] = target_id

    if not record_to_target:
        return [], stats

    parent: dict[str, str] = {}
    for rid, target_id in record_to_target.items():
        _union_find_merge(parent, rid, target_id)

    components: dict[str, set[str]] = defaultdict(set)
    for node in set(record_to_target.keys()) | set(record_to_target.values()):
        components[_union_find_root(parent, node)].add(node)

    groups: list[tuple[list[dict[str, Any]], str]] = []
    sym = ticker.strip().upper()
    for _root, member_ids in components.items():
        if member_ids & skip:
            continue
        wiki_target_id = _wiki_target_for_component(member_ids, record_to_target)
        target_rec = _load_record_for_event_id(wiki_target_id, records_by_id=records_by_id)
        if not target_rec:
            continue

        members: list[dict[str, Any]] = [target_rec]
        seen_ids: set[str] = {_record_event_id(target_rec)}
        for event_id in member_ids:
            if event_id == wiki_target_id:
                continue
            rec = _load_record_for_event_id(event_id, records_by_id=records_by_id)
            if not rec:
                continue
            rid = _record_event_id(rec)
            if not rid or rid in seen_ids:
                continue
            ref = _record_as_match_ref(rec)
            if not find_matching_event(ref, [target_rec], ticker=sym):
                continue
            members.append(rec)
            seen_ids.add(rid)
        if len(members) >= 2:
            groups.append((members, wiki_target_id))

    return groups, stats
