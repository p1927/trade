"""Async intent queue for bridge execution."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from nautilus_openalgo_bridge.handoff import intents_root
from nautilus_openalgo_bridge.models import ExecutionIntent
from nautilus_openalgo_bridge.openalgo_client import BridgeOpenAlgoClient, get_openalgo_client
from nautilus_openalgo_bridge.risk_state import is_trading_halted, should_skip_intent

logger = logging.getLogger(__name__)


def processed_intents_root() -> Path:
    root = intents_root() / "processed"
    root.mkdir(parents=True, exist_ok=True)
    return root


def halted_skipped_root() -> Path:
    root = intents_root() / "halted_skipped"
    root.mkdir(parents=True, exist_ok=True)
    return root


def submit_intent(intent: ExecutionIntent) -> Path:
    """Queue an intent for async execution by the watch node."""
    intents_root()
    intent_id = intent.intent_id or f"intent_{intent.created_at.replace(':', '').replace('-', '').replace('.', '')}"
    intent.intent_id = intent_id
    path = intents_root() / f"{intent_id}.json"
    path.write_text(json.dumps(intent.to_dict(), indent=2), encoding="utf-8")
    return path


def list_pending_intents() -> list[Path]:
    root = intents_root()
    return sorted(
        [path for path in root.glob("*.json") if path.is_file()],
        key=lambda item: item.stat().st_mtime,
    )


def archive_intent(intent: ExecutionIntent, result: dict[str, Any]) -> Path:
    processed_intents_root()
    intent_id = intent.intent_id or "intent_unknown"
    payload = intent.to_dict()
    payload["_execution_result"] = result
    path = processed_intents_root() / f"{intent_id}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def archive_halted_intent(intent: ExecutionIntent, *, reason: str = "trading_halted") -> Path:
    halted_skipped_root()
    intent_id = intent.intent_id or "intent_unknown"
    payload = intent.to_dict()
    payload["_execution_result"] = {"status": "halted_skipped", "reason": reason}
    path = halted_skipped_root() / f"{intent_id}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def process_pending_intents(
    *,
    client: BridgeOpenAlgoClient | None = None,
    max_count: int = 5,
) -> list[dict[str, Any]]:
    """Execute queued intents and move to processed/."""
    from nautilus_openalgo_bridge.execute import execute_intent

    oa = client or get_openalgo_client()
    results: list[dict[str, Any]] = []
    for path in list_pending_intents()[:max_count]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            intent = ExecutionIntent.from_dict(payload)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("skip invalid intent file %s: %s", path.name, exc)
            path.unlink(missing_ok=True)
            continue

        agent_id = (intent.agent_id or "").strip()
        if agent_id and is_trading_halted(agent_id):
            logger.warning("skip intent %s — trading halted for %s", path.name, agent_id)
            archive_halted_intent(intent)
            path.unlink(missing_ok=True)
            results.append({"intent_id": intent.intent_id, "status": "halted_skipped"})
            continue

        dedupe_key = intent.intent_id or json.dumps(intent.to_dict(), sort_keys=True)
        if agent_id and should_skip_intent(agent_id, dedupe_key):
            logger.warning("skip duplicate intent %s for %s", path.name, agent_id)
            archive_intent(intent, {"status": "duplicate_skipped"})
            path.unlink(missing_ok=True)
            results.append({"intent_id": intent.intent_id, "status": "duplicate_skipped"})
            continue

        result = execute_intent(intent, client=oa, persist=False)
        archive_intent(intent, result)
        path.unlink(missing_ok=True)
        results.append({"intent_id": intent.intent_id, **result})
        logger.info("processed intent %s → %s", intent.intent_id, result.get("status"))
    return results
