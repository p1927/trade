"""Thin bridge to self-hosted or cloud Skyvern for agentic browser tasks."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from trade_integrations.nse_browser.registry import hub_root
from trade_integrations.nse_browser.skyvern_local import read_local_skyvern_api_key

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"completed", "failed", "terminated", "timed_out", "canceled", "cancelled"})


def _truthy(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off"}


def skyvern_enabled() -> bool:
    return _truthy("SKYVERN_ENABLED", "1")


def skyvern_base_url() -> str:
    return os.environ.get("SKYVERN_BASE_URL", "http://localhost:8010").rstrip("/")


def skyvern_api_key() -> str:
    explicit = os.environ.get("SKYVERN_API_KEY", "").strip()
    if explicit:
        return explicit
    return read_local_skyvern_api_key()


def skyvern_configured() -> bool:
    if not skyvern_enabled():
        return False
    return bool(skyvern_api_key())


def _api_prefix() -> str:
    raw = os.environ.get("SKYVERN_API_PREFIX", "/v1").strip()
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return raw.rstrip("/")


def _api_url(path: str) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    base = skyvern_base_url()
    # Self-hosted image: heartbeat under /api/v1; task/run APIs under /v1
    if path.lstrip("/") == "heartbeat":
        return f"{base}/api/v1/heartbeat"
    prefix = _api_prefix()
    if path.startswith(prefix + "/") or path == prefix:
        return urljoin(base + "/", path.lstrip("/"))
    return urljoin(base + prefix + "/", path.lstrip("/"))


def skyvern_status() -> dict[str, Any]:
    """Reachability and config summary for status endpoints."""
    payload: dict[str, Any] = {
        "enabled": skyvern_enabled(),
        "configured": skyvern_configured(),
        "api_key_source": (
            "env"
            if os.environ.get("SKYVERN_API_KEY", "").strip()
            else ("local_credentials" if skyvern_api_key() else "missing")
        ),
        "llm_provider": "minimax_openai_compatible",
        "base_url": skyvern_base_url(),
        "api_prefix": _api_prefix(),
        "reachable": False,
        "heartbeat": None,
        "error": None,
    }
    if not skyvern_enabled():
        payload["error"] = "skyvern_disabled"
        return payload
    try:
        resp = requests.get(_api_url("/heartbeat"), timeout=5)
        payload["reachable"] = resp.status_code == 200
        if resp.status_code == 200:
            try:
                payload["heartbeat"] = resp.json()
            except Exception:
                payload["heartbeat"] = {"raw": resp.text[:200]}
        else:
            payload["error"] = f"heartbeat_http_{resp.status_code}"
    except Exception as exc:
        payload["error"] = str(exc)
    return payload


def _task_dir(task_id: str) -> Path:
    path = hub_root() / "tasks" / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _persist_task_artifact(task_id: str, name: str, data: Any) -> str:
    dest = _task_dir(task_id) / name
    if isinstance(data, (dict, list)):
        dest.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    else:
        dest.write_text(str(data), encoding="utf-8")
    return str(dest)


def _poll_run(run_id: str, *, max_wait_s: int) -> dict[str, Any]:
    headers = {"x-api-key": skyvern_api_key()}
    deadline = time.monotonic() + max_wait_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        resp = requests.get(_api_url(f"/runs/{run_id}"), headers=headers, timeout=30)
        if resp.status_code >= 400:
            return {"status": "error", "error": f"poll_http_{resp.status_code}", "body": resp.text[:500]}
        last = resp.json()
        status = str(last.get("status", "")).lower()
        if status in _TERMINAL_STATUSES:
            return last
        time.sleep(float(os.environ.get("SKYVERN_POLL_INTERVAL_S", "3")))
    return {**last, "status": "timed_out", "error": "poll_timeout"}


def run_skyvern_task(
    goal: str,
    *,
    url: str | None = None,
    output_schema: dict[str, Any] | None = None,
    max_wait_s: int | None = None,
    persist: bool = True,
    task_id: str | None = None,
) -> dict[str, Any]:
    """
    Run a Skyvern agent task and return structured output.

    Uses POST /api/v1/run/tasks then polls GET /api/v1/runs/{run_id}.
    """
    task_id = task_id or f"tsk_{uuid.uuid4().hex[:12]}"
    started = datetime.now(timezone.utc).isoformat()
    if not skyvern_configured():
        return {
            "status": "error",
            "task_id": task_id,
            "engine": "skyvern",
            "error": "skyvern_not_configured",
            "hint": "Start Skyvern (scripts/start_skyvern.sh) — local API key auto-read from .skyvern-data/.skyvern/credentials.toml",
        }

    wait_s = max_wait_s or int(os.environ.get("SKYVERN_TASK_TIMEOUT_S", "180"))
    body: dict[str, Any] = {"prompt": goal}
    if url:
        body["url"] = url
    if output_schema:
        body["data_extraction_schema"] = output_schema

    headers = {"x-api-key": skyvern_api_key(), "Content-Type": "application/json"}
    try:
        create = requests.post(_api_url("/run/tasks"), headers=headers, json=body, timeout=60)
    except Exception as exc:
        return {"status": "error", "task_id": task_id, "engine": "skyvern", "error": str(exc)}

    if create.status_code >= 400:
        return {
            "status": "error",
            "task_id": task_id,
            "engine": "skyvern",
            "error": f"create_http_{create.status_code}",
            "body": create.text[:800],
        }

    created = create.json()
    run_id = created.get("run_id") or created.get("task_id") or created.get("id")
    if not run_id:
        return {
            "status": "error",
            "task_id": task_id,
            "engine": "skyvern",
            "error": "missing_run_id",
            "body": created,
        }

    final = _poll_run(str(run_id), max_wait_s=wait_s)
    status = str(final.get("status", "unknown")).lower()
    output = final.get("output") or final.get("extracted_information") or final.get("data")
    if output is None and isinstance(final.get("result"), dict):
        output = final["result"].get("output")

    result: dict[str, Any] = {
        "status": "ok" if status == "completed" else "error",
        "task_id": task_id,
        "run_id": run_id,
        "engine": "skyvern",
        "skyvern_status": status,
        "goal": goal,
        "url": url,
        "structured_output": output,
        "summary": final.get("summary"),
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if status != "completed":
        result["error"] = final.get("failure_reason") or final.get("error") or status

    if persist:
        result["hub_path"] = _persist_task_artifact(task_id, "result.json", result)
        _persist_task_artifact(task_id, "skyvern_run.json", final)

    return result


def rows_from_skyvern_output(output: Any) -> list[dict[str, Any]]:
    """Normalize Skyvern extraction payload to list of row dicts."""
    if output is None:
        return []
    if isinstance(output, list):
        return [row for row in output if isinstance(row, dict)]
    if isinstance(output, dict):
        for key in ("table_rows", "rows", "records", "data"):
            val = output.get(key)
            if isinstance(val, list):
                return [row for row in val if isinstance(row, dict)]
        if any(k in output for k in ("date", "buy", "sell", "net", "fii_net", "dii_net")):
            return [output]
    return []
