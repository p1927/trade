"""Assemble financial expert context for external predictions agent."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.external_predictions.source_registry import (
    external_predictions_root,
)

logger = logging.getLogger(__name__)

_KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "knowledge"
_EXPERT_BRIEF = _KNOWLEDGE_DIR / "nifty_expert_brief.md"
_FACTOR_PLAYBOOK = _KNOWLEDGE_DIR / "factor_playbook.yaml"
_STRATEGY_PLAYBOOK = _KNOWLEDGE_DIR / "strategy_playbook.yaml"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def expert_context_path(symbol: str = "NIFTY") -> Path:
    return external_predictions_root(symbol) / "expert_context.json"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.debug("yaml load failed for %s: %s", path, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_expert_brief() -> str:
    if not _EXPERT_BRIEF.is_file():
        return ""
    try:
        return _EXPERT_BRIEF.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _india_trading_date() -> str:
    try:
        from trade_integrations.dataflows.company_research.market import india_trading_date_iso

        return india_trading_date_iso()
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()


def _load_internal_forecast(symbol: str, horizon_days: int) -> dict[str, Any] | None:
    try:
        from trade_integrations.context.hub import load_index_research_json

        doc = load_index_research_json(symbol)
        if doc is None:
            return None
        pred = getattr(doc, "prediction", None) or {}
        if not isinstance(pred, dict):
            pred = {}
        return {
            "direction": pred.get("direction") or pred.get("view"),
            "expected_return_pct": pred.get("expected_return_pct") or pred.get("return_pct"),
            "confidence": pred.get("confidence"),
            "horizon_days": horizon_days,
            "note": "Internal Ridge model — disambiguation only; do not copy as street target.",
        }
    except Exception as exc:
        logger.debug("internal forecast load failed: %s", exc)
        return None


def _load_factor_snapshot() -> dict[str, Any]:
    try:
        from trade_integrations.dataflows.index_research.news_market_context import (
            _fetch_factor_snapshot,
        )

        snap = _fetch_factor_snapshot()
        return snap if isinstance(snap, dict) else {}
    except Exception as exc:
        logger.debug("factor snapshot failed: %s", exc)
        return {}


def _load_interpretation_bundle(
    factors: dict[str, Any],
    *,
    horizon_days: int,
    symbol: str,
) -> dict[str, Any]:
    if not factors:
        return {}
    try:
        from trade_integrations.knowledge.interpret import build_index_interpretation_bundle

        return build_index_interpretation_bundle(
            factors,
            horizon_days=horizon_days,
            ticker=symbol,
        )
    except Exception as exc:
        logger.debug("interpretation bundle failed: %s", exc)
        return {}


def _top_factor_movers(factors: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
    playbook = _load_yaml(_FACTOR_PLAYBOOK).get("factors") or {}
    movers: list[dict[str, Any]] = []
    for key, value in factors.items():
        if value is None:
            continue
        meta = playbook.get(key) if isinstance(playbook, dict) else None
        label = key
        summary = ""
        if isinstance(meta, dict):
            label = str(meta.get("label") or key)
            summary = str(meta.get("summary") or "")
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        movers.append({"key": key, "label": label, "value": numeric, "summary": summary[:200]})
    movers.sort(key=lambda row: abs(float(row["value"])), reverse=True)
    return movers[:limit]


def build_expert_context(
    *,
    symbol: str = "NIFTY",
    horizon_days: int = 14,
    spot: float | None = None,
) -> dict[str, Any]:
    """Build JSON context pack for FinancialExpertAgent."""
    sym = symbol.upper()
    factors = _load_factor_snapshot()
    ctx: dict[str, Any] = {
        "symbol": sym,
        "horizon_days": int(horizon_days),
        "as_of": _india_trading_date(),
        "built_at": _now_iso(),
        "spot": spot,
        "expert_brief": _load_expert_brief(),
        "factor_playbook_keys": list((_load_yaml(_FACTOR_PLAYBOOK).get("factors") or {}).keys())[:20],
        "strategy_profiles": list((_load_yaml(_STRATEGY_PLAYBOOK).get("profiles") or {}).keys()),
        "top_factor_movers": _top_factor_movers(factors),
        "factors": factors,
        "interpretation": _load_interpretation_bundle(factors, horizon_days=horizon_days, symbol=sym),
        "internal_forecast": _load_internal_forecast(sym, horizon_days),
        "extraction_rules": [
            "NIFTY 50 index level targets only (15k–35k band).",
            "Reject single-stock, Sensex-only, options-only pages without index level.",
            "Extract published_at and target_date from article when visible.",
            "Flag horizon_match false when article horizon differs from tab horizon.",
        ],
    }
    if spot is None:
        try:
            from trade_integrations.dataflows.index_research.spot_fetch import fetch_index_spot

            result = fetch_index_spot(sym)
            if result.spot > 0:
                ctx["spot"] = result.spot
        except Exception:
            pass
    return ctx


def save_expert_context(ctx: dict[str, Any], *, symbol: str = "NIFTY") -> Path:
    path = expert_context_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ctx, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def load_expert_context(*, symbol: str = "NIFTY") -> dict[str, Any] | None:
    path = expert_context_path(symbol)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def build_and_save_expert_context(
    *,
    symbol: str = "NIFTY",
    horizon_days: int = 14,
    spot: float | None = None,
) -> dict[str, Any]:
    ctx = build_expert_context(symbol=symbol, horizon_days=horizon_days, spot=spot)
    save_expert_context(ctx, symbol=symbol)
    return ctx
