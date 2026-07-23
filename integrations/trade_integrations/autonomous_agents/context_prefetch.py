"""Run-time agent context for autonomous turns (injected at LLM, not stored in HTTP body)."""

from __future__ import annotations

import json
import re
from typing import Any

_TURN_KIND_RE = re.compile(
    r"#\s*Autonomous agent turn\s*\(([^)]+)\)",
    re.IGNORECASE,
)


def infer_turn_kind_from_prompt(content: str) -> str:
    match = _TURN_KIND_RE.search(content or "")
    if match:
        return str(match.group(1) or "").strip().lower() or "research"
    if "strategy revision" in (content or "").lower():
        return "strategy_revision"
    if "bootstrap checklist" in (content or "").lower():
        return "bootstrap"
    return "research"


def format_autonomous_context_for_prefetch(*, agent: dict[str, Any], turn_kind: str) -> str:
    """Expanded learning/progress/mandate detail for [agent_context] injection."""
    from trade_integrations.autonomous_agents.agent_learning import format_learning_context_for_prompt
    from trade_integrations.autonomous_agents.mandate import mandate_config_from_agent
    from trade_integrations.autonomous_agents.strategy_progress import (
        format_strategy_progress_for_prompt,
    )

    parts: list[str] = []
    learning = format_learning_context_for_prompt(agent=agent)
    if learning.strip():
        parts.append(learning.strip())
    progress = format_strategy_progress_for_prompt(agent=agent, turn_kind=turn_kind)
    if progress.strip():
        parts.append(progress.strip())
    if turn_kind in {"strategy_revision", "research", "bootstrap", "post_execution"}:
        mc = mandate_config_from_agent(agent)
        parts.append(f"## Mandate config\n```json\n{json.dumps(mc.to_dict(), indent=2)}\n```")
    if not parts:
        return ""
    return "[agent_context]\n" + "\n\n".join(parts)
