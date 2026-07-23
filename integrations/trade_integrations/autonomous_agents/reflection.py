"""End-of-session / post-trade reflection for agent memory."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir


def reflections_dir(*, agent_id: str | None = None) -> Path:
    root = get_hub_dir() / "_data" / "autonomous_agents" / "reflections"
    if agent_id:
        root = root / "".join(c for c in agent_id if c.isalnum() or c in "_-")
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_reflection(
    *,
    date_key: str | None = None,
    summary: str,
    decisions: list[dict[str, Any]] | None = None,
    pnl_inr: float | None = None,
    agent_id: str | None = None,
) -> Path:
    key = date_key or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = reflections_dir(agent_id=agent_id) / f"{key}.md"
    title = f"# Paper session reflection — {key}"
    if agent_id:
        title = f"# Paper session reflection — {agent_id} — {key}"
    lines = [title, ""]
    if pnl_inr is not None:
        lines.append(f"Net P&L: ₹{pnl_inr:,.0f}")
        lines.append("")
    lines.append(summary.strip())
    if decisions:
        lines.append("")
        lines.append("## Decisions")
        for d in decisions[-10:]:
            lines.append(f"- **{d.get('decision')}**: {d.get('rationale', '')[:200]}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def load_recent_reflections(*, limit: int = 3, agent_id: str | None = None) -> list[str]:
    if agent_id:
        root = reflections_dir(agent_id=agent_id)
        paths = sorted(root.glob("*.md"), reverse=True)[:limit]
    else:
        root = reflections_dir()
        paths = sorted(root.glob("*.md"), reverse=True)[:limit]
        agent_root = get_hub_dir() / "_data" / "autonomous_agents" / "reflections"
        if agent_root.is_dir():
            for sub in sorted(agent_root.iterdir()):
                if sub.is_dir():
                    paths.extend(sorted(sub.glob("*.md"), reverse=True))
            paths = sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            out.append(text)
    return out


def format_reflections_for_prompt(*, limit: int = 2, agent_id: str | None = None) -> str:
    blocks = load_recent_reflections(limit=limit, agent_id=agent_id)
    if not blocks:
        return ""
    joined = "\n\n---\n\n".join(blocks)
    return f"## Recent session reflections\n\n{joined}\n"
