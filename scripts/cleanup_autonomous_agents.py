#!/usr/bin/env python3
"""Stop and delete all autonomous test agents, proposals, and bridge artifacts."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

env_file = ROOT / ".env"
if env_file.is_file():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        val = value.strip().strip('"').strip("'")
        if val:
            os.environ[key.strip()] = val


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    api_key = os.getenv("VIBE_API_AUTH_KEY") or os.getenv("API_AUTH_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _vibe_base() -> str:
    return os.getenv("VIBE_BACKEND_URL", "http://127.0.0.1:8899").rstrip("/")


def vibe_request(method: str, path: str) -> dict | None:
    url = f"{_vibe_base()}{path}"
    req = urllib.request.Request(url, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        print(f"  warn {method} {path}: {exc.code}", flush=True)
        return None
    except urllib.error.URLError as exc:
        print(f"  warn vibe unreachable: {exc.reason}", flush=True)
        return None


def main() -> int:
    from trade_integrations.autonomous_agents.store import delete_agent, list_agents
    from trade_integrations.context.hub import get_hub_dir

    hub = get_hub_dir() / "_data"
    agents = list_agents()
    print(f"Cleaning {len(agents)} agent(s)...", flush=True)

    for agent in agents:
        agent_id = str(agent.get("id") or "")
        if not agent_id:
            continue
        vibe_request("POST", f"/autonomous-agents/{agent_id}/stop")
        vibe_request("DELETE", f"/autonomous-agents/{agent_id}")
        delete_agent(agent_id)
        print(f"  removed {agent_id}", flush=True)

    try:
        from trade_integrations.auto_paper.mcp_actions import stop_auto_paper

        stop_auto_paper()
        print("  stopped global auto_paper session", flush=True)
    except Exception as exc:
        print(f"  warn stop_auto_paper: {exc}", flush=True)

    proposals_dir = hub / "autonomous_agents" / "proposals"
    if proposals_dir.is_dir():
        for path in proposals_dir.glob("aap_*.json"):
            path.unlink(missing_ok=True)
            print(f"  removed proposal {path.name}", flush=True)

    for sub in ("nautilus_handoffs", "nautilus_intents"):
        artifact_dir = hub / sub
        if artifact_dir.is_dir():
            for path in artifact_dir.glob("*.json"):
                path.unlink(missing_ok=True)
                print(f"  removed {sub}/{path.name}", flush=True)

    remaining = list_agents()
    print(f"Done — {len(remaining)} agent(s) remaining.", flush=True)
    return 0 if not remaining else 1


if __name__ == "__main__":
    raise SystemExit(main())
