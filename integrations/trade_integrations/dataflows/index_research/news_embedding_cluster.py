"""Tier-2 semantic pre-clustering for staging refs.

Uses LLM-Wiki's embedding endpoint when available (``HUB_NEWS_USE_LLM_WIKI_EMBEDDINGS=1``),
otherwise falls back to stdlib ``summary_similarity``.
"""

from __future__ import annotations

import os
from typing import Any

from trade_integrations.dataflows.index_research.news_event_matching import summary_similarity
from trade_integrations.hub_storage.news_merge_ledger import append_merge_event


def cluster_threshold() -> float:
    try:
        from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

        return float(load_news_pipeline_config().cluster_threshold)
    except Exception:
        pass
    try:
        return float(os.getenv("HUB_NEWS_EMBED_CLUSTER_THRESHOLD", "0.85"))
    except ValueError:
        return 0.85


def _ref_text(ref: dict[str, Any]) -> str:
    title = str(ref.get("title") or "")
    summary = str(ref.get("summary") or "")
    return f"{title} {summary}".strip()


def _similarity(
    text_a: str,
    text_b: str,
    *,
    vec_a: list[float] | None = None,
    vec_b: list[float] | None = None,
) -> float:
    try:
        from trade_integrations.dataflows.hub_wiki.embeddings import text_similarity

        return text_similarity(text_a, text_b, vec_a=vec_a, vec_b=vec_b)
    except Exception:
        return summary_similarity(text_a, text_b)


def _embed_texts(texts: list[str]) -> list[list[float] | None]:
    try:
        from trade_integrations.dataflows.hub_wiki.embeddings import (
            embedding_available,
            fetch_embeddings_batch,
        )

        if not embedding_available():
            return [None] * len(texts)
        return fetch_embeddings_batch(texts)
    except Exception:
        return [None] * len(texts)


def assign_cluster_ids(refs: list[dict[str, Any]], *, threshold: float | None = None) -> list[dict[str, Any]]:
    """Greedy cluster assignment; sets ``cluster_id`` on each ref dict."""
    cut = cluster_threshold() if threshold is None else threshold
    texts = [_ref_text(ref) for ref in refs]
    vectors = _embed_texts(texts)
    backend = "llm_wiki_embed" if any(v is not None for v in vectors) else "stdlib"

    clusters: list[list[dict[str, Any]]] = []
    cluster_vectors: list[list[float] | None] = []
    for ref, text, vec in zip(refs, texts, vectors):
        placed = False
        for idx, cluster in enumerate(clusters):
            leader_vec = cluster_vectors[idx]
            sim = _similarity(text, _ref_text(cluster[0]), vec_a=vec, vec_b=leader_vec)
            if sim >= cut:
                cluster.append(ref)
                ref["cluster_id"] = str(cluster[0].get("ref_id") or cluster[0].get("cluster_id") or "")
                ref["cluster_backend"] = backend
                placed = True
                break
        if not placed:
            rid = str(ref.get("ref_id") or "")
            ref["cluster_id"] = rid
            ref["cluster_backend"] = backend
            clusters.append([ref])
            cluster_vectors.append(vec)
    return refs


def dedupe_pending_by_cluster(
    refs: list[dict[str, Any]],
    *,
    ticker: str | None = None,
    threshold: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Keep one ref per cluster; mark duplicates merged (tier-2 audit)."""
    from trade_integrations.hub_storage.news_staging_store import mark_ref_merged

    if not refs:
        return [], {"input": 0, "kept": 0, "dropped": 0, "clusters": 0}

    assigned = assign_cluster_ids(list(refs), threshold=threshold)
    kept: list[dict[str, Any]] = []
    dropped = 0
    sym = (ticker or "NIFTY").strip().upper()
    backend = str(assigned[0].get("cluster_backend") or "stdlib") if assigned else "stdlib"

    by_cluster: dict[str, list[dict[str, Any]]] = {}
    for ref in assigned:
        cid = str(ref.get("cluster_id") or ref.get("ref_id") or "")
        by_cluster.setdefault(cid, []).append(ref)

    reason = "tier2_llm_wiki_embed" if backend == "llm_wiki_embed" else "tier2_embedding_cluster"

    for cid, group in by_cluster.items():
        leader = group[0]
        kept.append(leader)
        for dup in group[1:]:
            dropped += 1
            dup_id = str(dup.get("ref_id") or "")
            try:
                append_merge_event(
                    ticker=sym,
                    event_id=f"cluster:{cid}",
                    canonical_story_id=str(leader.get("ref_id") or cid),
                    merged_story_ids=[dup_id],
                    ref_count=len(group),
                    reason=reason,
                    title=str(dup.get("title") or "")[:200],
                )
                if dup_id:
                    mark_ref_merged(dup_id, f"cluster:{cid}")
            except Exception:
                pass

    return kept, {
        "input": len(refs),
        "kept": len(kept),
        "dropped": dropped,
        "clusters": len(by_cluster),
        "backend": backend,
    }
