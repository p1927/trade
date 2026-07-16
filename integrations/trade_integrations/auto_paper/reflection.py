"""End-of-session / post-trade reflection for agent memory."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trade_integrations.context.hub import get_hub_dir


def reflections_dir() -> Path:
    path = get_hub_dir() / "_data" / "auto_paper" / "reflections"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_reflection(
    *,
    date_key: str | None = None,
    summary: str,
    decisions: list[dict[str, Any]] | None = None,
    pnl_inr: float | None = None,
) -> Path:
    key = date_key or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = reflections_dir() / f"{key}.md"
    lines = [f"# Paper session reflection — {key}", ""]
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


def load_recent_reflections(limit: int = 3) -> list[str]:
    root = reflections_dir()
    paths = sorted(root.glob("*.md"), reverse=True)[:limit]
    out: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            out.append(text)
    return out


def format_reflections_for_prompt(limit: int = 2) -> str:
    blocks = load_recent_reflections(limit=limit)
    if not blocks:
        return ""
    joined = "\n\n---\n\n".join(blocks)
    return f"## Recent session reflections\n\n{joined}\n"
