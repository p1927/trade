"""HTTP client for the local LLM-Wiki API (default http://127.0.0.1:19828)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from trade_integrations.dataflows.hub_wiki.config import (
    get_llm_wiki_project_dir,
    llm_wiki_api_token,
    llm_wiki_base_url,
    llm_wiki_project_id,
)


def _request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    url = f"{llm_wiki_base_url()}{path}"
    headers = {"Accept": "application/json"}
    token = llm_wiki_api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return {"ok": False, "error": detail or str(exc), "status": exc.code}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": str(exc.reason), "reachable": False}


def health_check() -> dict[str, Any]:
    """GET /api/v1/health — no auth required."""
    return _request("GET", "/api/v1/health")


def list_projects() -> dict[str, Any]:
    return _request("GET", "/api/v1/projects")


def resolve_project_id() -> str | None:
    """Env LLM_WIKI_PROJECT_ID, else current project from API."""
    configured = llm_wiki_project_id()
    if configured:
        return configured
    payload = list_projects()
    if not payload.get("ok"):
        return None
    current = payload.get("currentProject") or {}
    pid = str(current.get("id") or "").strip()
    return pid or None


def resolve_registered_project(*, project_id: str | None = None) -> dict[str, Any] | None:
    """Return the project dict from API matching id or current."""
    pid = project_id or resolve_project_id()
    payload = list_projects()
    if not payload.get("ok"):
        return None
    projects = payload.get("projects") or []
    if isinstance(projects, list):
        for row in projects:
            if not isinstance(row, dict):
                continue
            if pid and str(row.get("id") or "") == pid:
                return row
        current = payload.get("currentProject")
        if isinstance(current, dict):
            return current
    return None


def project_path_aligned(*, expected_dir: Path | None = None) -> dict[str, Any]:
    """Check desktop-registered project path matches Trade hub llm-wiki dir."""
    expected = (expected_dir or get_llm_wiki_project_dir()).resolve()
    registered = resolve_registered_project()
    if not registered:
        return {
            "aligned": False,
            "expected_path": str(expected),
            "registered_path": None,
            "reason": "no_registered_project",
        }
    reg_path = Path(str(registered.get("path") or "")).resolve()
    aligned = reg_path == expected
    return {
        "aligned": aligned,
        "expected_path": str(expected),
        "registered_path": str(reg_path),
        "project_name": registered.get("name"),
        "project_id": registered.get("id"),
        "current": bool(registered.get("current")),
    }


def list_project_files(
    *,
    root: str = "all",
    project_id: str | None = None,
    max_files: int = 2000,
) -> dict[str, Any]:
    pid = project_id or resolve_project_id()
    if not pid:
        return {"ok": False, "error": "LLM_Wiki project id not configured"}
    q = urllib.parse.urlencode({"root": root, "recursive": "true", "maxFiles": str(max_files)})
    return _request("GET", f"/api/v1/projects/{pid}/files?{q}")


def _count_files(node: dict[str, Any]) -> int:
    if not node.get("isDir"):
        return 1
    total = 0
    for child in node.get("children") or []:
        if isinstance(child, dict):
            total += _count_files(child)
    return total


def count_project_files(*, root: str = "wiki", project_id: str | None = None) -> int:
    payload = list_project_files(root=root, project_id=project_id)
    if not payload.get("ok"):
        return 0
    total = 0
    for node in payload.get("files") or []:
        if isinstance(node, dict):
            total += _count_files(node)
    return total


def trigger_sources_rescan(*, project_id: str | None = None) -> dict[str, Any]:
    """POST /api/v1/projects/{id}/sources/rescan after writing source files."""
    pid = project_id or resolve_project_id()
    if not pid:
        return {"ok": False, "error": "LLM_Wiki project id not configured and API unavailable"}
    return _request("POST", f"/api/v1/projects/{pid}/sources/rescan")


def search_wiki(
    query: str,
    *,
    top_k: int = 10,
    include_content: bool = False,
    project_id: str | None = None,
) -> dict[str, Any]:
    pid = project_id or resolve_project_id()
    if not pid:
        return {"ok": False, "error": "LLM_Wiki project id not configured"}
    return _request(
        "POST",
        f"/api/v1/projects/{pid}/search",
        body={"query": query, "topK": top_k, "includeContent": include_content},
    )
