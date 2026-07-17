"""Bind news-scenario tools to a frozen Analysis pipeline snapshot (no re-fetch)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.models import IndexResearchDoc

logger = logging.getLogger(__name__)


class PipelineSnapshotError(Exception):
    """Base error for snapshot binding failures."""

    code: str = "snapshot_error"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self)}


class MissingSnapshotError(PipelineSnapshotError):
    code = "missing_snapshot"


class StaleSnapshotError(PipelineSnapshotError):
    code = "stale_snapshot"


def normalize_as_of(value: datetime | str | None) -> str:
    """Normalize timestamps to UTC ISO with second precision for comparison."""
    if value is None:
        return ""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw[:19]
    elif isinstance(value, datetime):
        dt = value
    else:
        return str(value)[:19]

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).isoformat()


def _index_research_json_path(ticker: str) -> Path:
    key = ticker.strip().upper()
    return get_hub_dir() / key / "index_research" / "latest.json"


def load_pipeline_doc_from_hub(ticker: str) -> IndexResearchDoc | None:
    """Load index research JSON without news_impact side-effect refresh."""
    path = _index_research_json_path(ticker)
    if not path.is_file():
        return None
    from trade_integrations.context.hub import _index_doc_from_json

    payload = json.loads(path.read_text(encoding="utf-8"))
    return _index_doc_from_json(payload)


def load_model_artifact_for_snapshot(
    pipeline_as_of: str,
    *,
    allow: bool = True,
) -> dict[str, Any] | None:
    """Load stored Ridge artifact; warn if trained after the bound pipeline run."""
    if not allow:
        return None
    try:
        from trade_integrations.dataflows.index_research.factor_store import load_model_artifact

        artifact = load_model_artifact()
    except Exception:
        return None
    if not artifact:
        return None

    trained_raw = artifact.get("trained_at") or artifact.get("as_of")
    if trained_raw:
        trained_norm = normalize_as_of(str(trained_raw))
        bound_norm = normalize_as_of(pipeline_as_of)
        if trained_norm and bound_norm and trained_norm > bound_norm:
            logger.warning(
                "model_artifact trained_at %s is newer than pipeline_as_of %s; prefer doc.prediction.equation",
                trained_norm,
                bound_norm,
            )
    return artifact


def resolve_bound_pipeline_doc(
    ticker: str,
    pipeline_as_of: str,
    *,
    allow_model_artifact: bool = True,
) -> tuple[IndexResearchDoc, dict[str, Any] | None]:
    """Load hub doc and verify it matches the session-bound pipeline timestamp."""
    bound = normalize_as_of(pipeline_as_of)
    if not bound:
        raise MissingSnapshotError("pipeline_as_of is required")

    doc = load_pipeline_doc_from_hub(ticker)
    if doc is None or doc.spot is None:
        raise MissingSnapshotError(
            f"No index research snapshot for {ticker.strip().upper()}; run Analysis first"
        )

    doc_norm = normalize_as_of(doc.as_of)
    if doc_norm != bound:
        raise StaleSnapshotError(
            f"Hub snapshot as_of {doc_norm} does not match bound pipeline_as_of {bound}; restart session"
        )

    model = load_model_artifact_for_snapshot(bound, allow=allow_model_artifact)
    return doc, model


def snapshot_summary(doc: IndexResearchDoc) -> dict[str, Any]:
    """Compact summary for MCP tools and agent context."""
    pred = doc.prediction or {}
    range_block = pred.get("range") if isinstance(pred.get("range"), dict) else {}
    contributors = (doc.factor_explanation or {}).get("contributors") or []
    news_items = (doc.news_impact or {}).get("items") or []
    return {
        "ticker": doc.ticker,
        "as_of": normalize_as_of(doc.as_of),
        "spot": doc.spot,
        "horizon": doc.horizon,
        "prediction": {
            "view": pred.get("view"),
            "expected_return_pct": pred.get("expected_return_pct"),
            "bottom_up_return_pct": pred.get("bottom_up_return_pct"),
            "macro_delta_pct": pred.get("macro_delta_pct"),
            "range_low": range_block.get("low"),
            "range_high": range_block.get("high"),
        },
        "regime": doc.regime,
        "top_contributors": contributors[:6],
        "constituent_count": len(doc.constituent_signals or []),
        "news_item_count": len(news_items),
        "scenario_count": len(doc.scenarios or []),
    }
