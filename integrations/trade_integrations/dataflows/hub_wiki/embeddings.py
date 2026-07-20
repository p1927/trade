"""Embedding helpers — reuse LLM-Wiki's configured embedding endpoint for hub dedup.

LLM-Wiki v0.6.4 exposes hybrid search (keyword + LanceDB vectors) but does **not**
expose a dedup HTTP API. Maintenance dedup runs in the desktop UI on wiki pages.
For staging tier-2 clustering we call the same OpenAI-compatible embedding endpoint
LLM-Wiki uses for semantic search (read from app-state or env overrides).
"""

from __future__ import annotations

import json
import logging
import math
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CACHE: dict[str, list[float]] = {}


def use_llm_wiki_embeddings() -> bool:
    raw = os.getenv("HUB_NEWS_USE_LLM_WIKI_EMBEDDINGS", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def llm_wiki_app_state_path() -> Path:
    override = os.getenv("LLM_WIKI_APP_STATE_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support/com.llmwiki.app/app-state.json"


def load_embedding_config() -> dict[str, Any] | None:
    """Env overrides win; else read LLM-Wiki desktop ``embeddingConfig``."""
    endpoint = (
        os.getenv("HUB_NEWS_EMBED_ENDPOINT", "").strip()
        or os.getenv("LLM_WIKI_EMBED_ENDPOINT", "").strip()
    )
    if endpoint:
        return {
            "enabled": True,
            "endpoint": endpoint,
            "model": os.getenv("HUB_NEWS_EMBED_MODEL", os.getenv("LLM_WIKI_EMBED_MODEL", "text-embedding-3-small")),
            "apiKey": os.getenv("HUB_NEWS_EMBED_API_KEY", os.getenv("LLM_WIKI_EMBED_API_KEY", "")),
        }

    path = llm_wiki_app_state_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        emb = data.get("embeddingConfig")
        if isinstance(emb, dict) and emb.get("enabled") and str(emb.get("endpoint") or "").strip():
            return emb
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("LLM-Wiki embedding config unreadable: %s", exc)
    return None


def embedding_available() -> bool:
    if not use_llm_wiki_embeddings():
        return False
    cfg = load_embedding_config()
    return bool(cfg and cfg.get("enabled") and cfg.get("endpoint"))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return float(dot / (na * nb))


def _parse_embedding_response(data: dict[str, Any]) -> list[float]:
    if "embedding" in data and isinstance(data["embedding"], dict):
        values = data["embedding"].get("values")
        if isinstance(values, list):
            return [float(v) for v in values if isinstance(v, (int, float))]
    rows = data.get("data")
    if isinstance(rows, list) and rows:
        emb = rows[0].get("embedding") if isinstance(rows[0], dict) else None
        if isinstance(emb, list):
            return [float(v) for v in emb if isinstance(v, (int, float))]
    raise ValueError("embedding response missing vector")


def fetch_embedding(text: str, *, cfg: dict[str, Any] | None = None) -> list[float] | None:
    """Single-text embed via LLM-Wiki's OpenAI-compatible endpoint."""
    config = cfg or load_embedding_config()
    if not config or not config.get("enabled"):
        return None
    endpoint = str(config.get("endpoint") or "").strip()
    if not endpoint or not text.strip():
        return None

    model = str(config.get("model") or "text-embedding-3-small")
    headers = {"Content-Type": "application/json"}
    api_key = str(config.get("apiKey") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for key, value in (config.get("extraHeaders") or {}).items():
        if key and value is not None:
            headers[str(key)] = str(value)

    body = json.dumps({"model": model, "input": text[:8000]}).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        vector = _parse_embedding_response(payload)
        return vector if vector else None
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as exc:
        logger.debug("embedding request failed: %s", exc)
        return None


def fetch_embeddings_batch(texts: list[str]) -> list[list[float] | None]:
    """Embed each text; small batches only (staging drain limit ≤200)."""
    out: list[list[float] | None] = []
    cfg = load_embedding_config()
    for text in texts:
        key = text.strip()[:8000]
        if key in _CACHE:
            out.append(_CACHE[key])
            continue
        vec = fetch_embedding(key, cfg=cfg)
        if vec is not None:
            _CACHE[key] = vec
        out.append(vec)
    return out


def text_similarity(
    a: str,
    b: str,
    *,
    vec_a: list[float] | None = None,
    vec_b: list[float] | None = None,
) -> float:
    """Cosine similarity when vectors exist; else stdlib summary similarity."""
    if vec_a and vec_b:
        return cosine_similarity(vec_a, vec_b)
    from trade_integrations.dataflows.index_research.news_event_matching import summary_similarity

    return summary_similarity(a, b)
